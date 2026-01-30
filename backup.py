import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import io
import time
import re
import urllib.parse
from collections import deque

class GlobalManager:
    def __init__(self):
        # Watch list
        self.watch_list = []
        # Logs
        self.logs = deque(maxlen=50)

        self.mile_threshold = 300  # Default value
        self.mins_threshold = 30   # Default value
        self.is_running = False
        # --- CRITICAL FIX: Session managed here, not in st.session_state ---
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self.available_accounts = [] 
        self.current_account_name = "Can't Find!"
        self.current_account_id = None
        
    def add_log(self, message, type="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = "ℹ️"
        if type == "success": icon = "✅"
        elif type == "error": icon = "❌"
        elif type == "warning": icon = "⚠️"
        
        log_entry = f"{timestamp} {icon} {message}"
        self.logs.appendleft(log_entry)
        print(log_entry)

    def set_mile_threshold(self, val):
        self.mile_threshold = val

    def set_mins_threshold(self, val):
        self.mins_threshold = val

    def update_watch_list(self, new_list):
        self.watch_list = new_list

    def get_watch_list_df(self):
        return pd.DataFrame(self.watch_list)

@st.cache_resource
def get_manager():
    return GlobalManager()

manager = get_manager()


# --- KONFIGURASYON ---
try:
    TEAMS_WEBHOOK_URL = st.secrets["TEAMS_WEBHOOK"]
    USER_EMAIL = st.secrets["DB_EMAIL"]
    USER_PASS = st.secrets["DB_PASS"]
except:
    TEAMS_WEBHOOK_URL = ""
    USER_EMAIL = ""
    USER_PASS = ""

BASE_URL = "https://app.2dworkflow.com"
LOGIN_URL = f"{BASE_URL}/login.jsf"
DRAFT_PAGE_URL = f"{BASE_URL}/draft.jsf"
PLAN_URL = f"{BASE_URL}/draftplan.jsf"

# --- FONKSİYONLAR ---

def login():
    """Siteye giriş yapar."""

    try:
        # Önce login sayfasına gidip ViewState alalım

        manager.session.cookies.clear()

        res = manager.session.get(LOGIN_URL)
        soup = BeautifulSoup(res.text, 'html.parser')
        view_state_input = soup.find("input", {"name": "javax.faces.ViewState"})
        button_id = soup.find("button").get("id")

        if not view_state_input:
            print("HATA: Login sayfasında ViewState bulunamadı.")
            return False
        view_state = view_state_input.get('value')

        payload = {
            "mainForm": "mainForm",
            "mainForm:email": USER_EMAIL,
            "mainForm:password": USER_PASS,
            button_id: "",
            "javax.faces.ViewState": view_state
        }

        post_res = manager.session.post(LOGIN_URL, data=payload, headers={"Referer": LOGIN_URL})

        # Başarılı login kontrolü:
        # JSF genelde hata verirse aynı sayfada kalır, başarırsa redirect eder.
        # URL hala login.jsf ise veya içerikte hata mesajı varsa başarısızdır.
        if "login.jsf" in post_res.url and "ui-messages-error" in post_res.text:
            print("Login Başarısız: Hata mesajı algılandı.")
            return False
        print(f"Login isteği sonucu: {post_res.status_code}, URL: {post_res.url}")

        fetch_accounts_backend(DRAFT_PAGE_URL)

        return True

    except Exception as e:
        print(f"Login işlem hatası: {e}")

        return False

def fetch_accounts_backend(current_url=DRAFT_PAGE_URL):
    """
    1. Gets the current page to find out who we are logged in as (ccFlag).
    2. Opens the menu to get the list of available accounts.
    """
    try:
        # --- ADIM 1: MEVCUT HESABI BUL (GET İSTEĞİ) ---
        res_page = manager.session.get(current_url)
        # Login ekranına attıysa dur
        if "login.jsf" in res_page.url: 
            print("Login gerekli.")
            return False

        soup_page = BeautifulSoup(res_page.text, 'html.parser')
        
        # Sayfanın tepesindeki bayrak/isim alanını bul (id="ccFlag")
        active_account_name = "Bilinmiyor"
        cc_flag_div = soup_page.find("div", id="ccFlag")
        
        if cc_flag_div:
            # Span içindeki texti al (örn: " Babil Design")
            span_text = cc_flag_div.get_text(strip=True)
            if span_text:
                active_account_name = span_text
                manager.current_account_name = active_account_name
                print(f"✅ Aktif Hesap Tespit Edildi: {active_account_name}")
        else:
            print("⚠️ ccFlag bulunamadı, aktif hesap adı çekilemedi.")

        # --- ADIM 2: HESAP LİSTESİNİ ÇEK (POST İSTEĞİ) ---
        # Menu butonuna basıp listeyi alıyoruz
        form_data = form_verilerini_topla(res_page.text)
        menu_btn_id = None
        
        # Strategy B: Fallback to onclick content if A fails
        if not menu_btn_id:
            link = soup_page.find("a", onclick=re.compile(r"__my_store__"))
            if link: menu_btn_id = link.get("id")

        # Strategy A: Look for Amazon Icon
        icon = soup_page.find("i", class_="fa-amazon")
        if icon:
            parent = icon.find_parent("a")
            if parent: menu_btn_id = parent.get("id")
            
        if not menu_btn_id:
            print("❌ Could not find the Account Menu button ID.")
            return False
        
        payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": menu_btn_id,
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "__my_store_form__:__my_stor_table__",
            menu_btn_id: menu_btn_id,
            "formLogo": "formLogo",
            "javax.faces.ViewState": form_data.get("javax.faces.ViewState", "")
        }
        
        res_menu = manager.session.post(current_url, data=payload)
        
        # XML Parse
        outer_soup = BeautifulSoup(res_menu.text, 'xml')
        update_tag = outer_soup.find('update', {'id': '__my_store_form__:__my_stor_table__'})
        
        if not update_tag:
            print("Hesap tablosu XML içinde bulunamadı.")
            return False

        inner_html = update_tag.text
        inner_soup = BeautifulSoup(inner_html, 'html.parser')
        rows = inner_soup.find_all("tr", attrs={"data-rk": True})
        
        new_accounts_list = []
        
        for row in rows:
            rk_id = row['data-rk']
            
            # İsmi input değerinden al
            name_input = row.find("input", id=lambda x: x and "store_name" in x)
            name = name_input['value'] if name_input else row.get_text(strip=True)
            
            # --- AKTİFLİK KONTROLÜ ---
            # Tablodaki isim ile yukarıda bulduğumuz aktif isim aynı mı?
            # (Küçük/büyük harf duyarlılığını kaldırmak için .strip() kullanıyoruz)
            is_active = (name.strip() == active_account_name.strip())
            if is_active:
                manager.current_account_id = rk_id
            new_accounts_list.append({
                "id": rk_id,
                "name": name,
                "flag": "🇺🇸", 
                "is_active": is_active
            })
            
        manager.available_accounts = new_accounts_list
        return True

    except Exception as e:
        print(f"Hesap çekme hatası: {e}")
        return False

def switch_account_backend(account_rk, current_url=DRAFT_PAGE_URL):
    """
    Switches the account using the row key (data-rk).
    """
    try:
        manager.add_log("Hesap değiştiriliyor...", "info")
        
        # We need the current ViewState and also the form data from the account list 
        # (because JSF often requires the values of the inputs in the table to be sent back)
        
        # 1. Trigger fetch again to ensure we have the latest table state/ViewState to submit
        # Or simply use the page we are on. Let's assume we are on DRAFT_PAGE_URL.
        res_page = manager.session.get(current_url)
        form_data = form_verilerini_topla(res_page.text)
        
        # We need to construct the specific payload for row selection
        # Note: We need to recreate the inputs for the table rows (store_name) 
        # usually found in the form data if the modal was rendered.
        
        # Since the modal might not be in the DOM of the main page GET request, 
        # we might need to manually construct the minimal payload.
        
        payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "__my_store_form__:__my_stor_table__",
            "javax.faces.partial.execute": "__my_store_form__:__my_stor_table__",
            "javax.faces.partial.render": "ccFlag contentPanel mainForm menuform",
            "javax.faces.behavior.event": "rowSelect",
            "javax.faces.partial.event": "rowSelect",
            "__my_store_form__:__my_stor_table___instantSelectedRowKey": account_rk,
            "__my_store_form__": "__my_store_form__",
            "__my_store_form__:__my_stor_table__:j_idt26:filter": "",
            "__my_store_form__:__my_stor_table___selection": account_rk,
            "__my_store_form__:__my_stor_table___scrollState": "0,0",
            "javax.faces.ViewState": form_data.get("javax.faces.ViewState", "")
        }
        
        # Sending request
        res = manager.session.post(current_url, data=payload)
        
        # Check for success (Look for ccFlag update which shows the new name)
        if "update id=\"ccFlag\"" in res.text:
            # Refresh accounts list to update 'active' status in our UI
            fetch_accounts_backend() 
            manager.add_log("✅ Hesap başarıyla değiştirildi.", "success")
            return True
        else:
            manager.add_log("❌ Hesap değiştirme başarısız oldu.", "error")
            return False
            
    except Exception as e:
        manager.add_log(f"Switch error: {e}", "error")
        return False

def form_verilerini_topla(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    form = soup.find("form", id="mainForm")
    if not form: return {}
    payload = {}
    for tag in form.find_all(["input", "select"]):
        name = tag.get("name")
        if not name: continue
        if tag.name == "input":
            value = tag.get("value", "")
            if tag.get("type") in ["checkbox", "radio"]:
                if tag.has_attr("checked"): payload[name] = value
            else: payload[name] = value
        elif tag.name == "select":
            selected = tag.find("option", selected=True)
            payload[name] = selected.get("value", "") if selected else ""
    return payload

def html_tabloyu_parse_et(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    rows = soup.find_all("tr", role="row")
    if not rows: return pd.DataFrame()

    watchlist_df = manager.get_watch_list_df()
    if not watchlist_df.empty and "date" in watchlist_df.columns:
        takip_edilen_tarihler = set(watchlist_df["date"].values)
    else:
        takip_edilen_tarihler = set()
    
    veri_listesi = []
    for row in rows:
        cells = row.find_all("td")
        if not cells or len(cells) < 11: continue
        try:
            name_input = cells[2].find("input")
            draft_name = name_input['value'] if name_input else cells[2].get_text(strip=True)
            name_input_id = name_input["id"]
            open_link = row.find("a", title="Open Draft Shipment")
            if not open_link: open_link = cells[1].find("a") 
            row_action_id = open_link.get("id") if open_link else None
            
            # Copy butonu bulma
            copy_link = row.find("a", title=lambda x: x and ("duplicate" in x.lower() or "copy" in x.lower()))
            if not copy_link:
                copy_icon = row.find("span", class_=lambda x: x and ("copy" in x or "clone" in x))
                if copy_icon: copy_link = copy_icon.find_parent("a")
            copy_action_id = copy_link.get("id") if copy_link else None

            from_loc = cells[3].get_text(strip=True)
            created_date = cells[10].get_text(strip=True)
            units = cells[9].get_text(strip=True)
            skus = cells[8].get_text(strip=True)
            
            # --- AUTO SELECT MANTIĞI ---
            # Eğer bu draft ismi, oluşturduğumuz kopyalar listesindeyse TRUE yap
            

            secili_mi = created_date in takip_edilen_tarihler
            veri_listesi.append({
                "Seç": secili_mi, # Dinamik seçim
                "Draft Name": draft_name,
                "From": from_loc,
                "SKUs": skus,
                "Units": units,
                "Created": created_date,
                "Action ID": row_action_id,
                "Copy ID": copy_action_id,
                "Name Input ID": name_input_id
            })
            
        except Exception as e: 
            print(e)
            continue
    return pd.DataFrame(veri_listesi)

def veriyi_dataframe_yap():
    if not manager.session.cookies:
        if not login(): return None, "Giriş Yapılamadı"
    try:
        response = manager.session.get(DRAFT_PAGE_URL)
        if "login.jsf" in response.url: login(); response = manager.session.get(DRAFT_PAGE_URL, headers={"Referer": DRAFT_PAGE_URL})
        df = html_tabloyu_parse_et(response.text)
        
        if not df.empty:
            # --- NEW CONFIG COLUMNS ---
            # 1. Specific Mile Limit (Defaults to Global Setting)
            df["Max Mil"] = manager.mile_threshold 
            # 2. Target Warehouses (Empty by default)
            df["Hedef Depolar"] = "" 
            
            return (df, None)
        else:
            return (None, "Tablo boş.")
    except Exception as e: return None, str(e)

def teams_bildirim_gonder(title, message, facts=None, status="info"):
    """
    Sends a high-contrast Adaptive Card with dividers between items.
    """
    # 1. Color and Icon Logic
    status_map = {
        "success": ("good", "✅"), 
        "error": ("attention", "❌"), 
        "warning": ("warning", "⚠️"), 
        "info": ("accent", "ℹ️")
    }
    theme_color, icon = status_map.get(status, ("accent", "ℹ️"))
    
    # 2. Construct Base Card Body
    card_body = [
        # --- HEADER (Color Bar) ---
        {
            "type": "Container",
            "style": theme_color,
            "padding": "Default",
            "items": [
                {
                    "type": "TextBlock",
                    "text": f"{icon} {title}",
                    "weight": "Bolder",
                    "size": "Medium",
                    "color": "Light" if status == "error" else "Default"
                }
            ]
        },
        # --- MESSAGE BODY ---
        {
            "type": "Container",
            "padding": "Default",
            "items": [
                {
                    "type": "TextBlock",
                    "text": message,
                    "wrap": True,
                    "isSubtle": False,  # <--- CHANGED: Makes text bright/readable
                    "size": "Default"   
                }
            ]
        }
    ]

    # 3. Dynamic Rows with Dividers (Replaces FactSet)
    if facts:
        # Create a container for the list
        list_container = {
            "type": "Container",
            "padding": "None",
            "items": []
        }
        
        first_item = True
        for k, v in facts.items():
            # Create a 2-Column Row for each fact
            row = {
                "type": "ColumnSet",
                "spacing": "Medium",      # Adds vertical space
                "separator": not first_item, # Adds line (divider) to all except the first
                "columns": [
                    {
                        "type": "Column",
                        "width": "auto", # Key takes only needed space
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": str(k),
                                "weight": "Bolder",
                                "wrap": True
                            }
                        ]
                    },
                    {
                        "type": "Column",
                        "width": "stretch", # Value takes remaining space
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": str(v),
                                "wrap": True,
                                "horizontalAlignment": "Right" # Aligns value to the right
                            }
                        ]
                    }
                ]
            }
            list_container["items"].append(row)
            first_item = False
            
        # Add the list container to the main card
        card_body.append({
            "type": "Container",
            "padding": "Default", # Adds padding around the whole list
            "style": "emphasis",  # Adds a slight background color to the data section
            "items": [list_container]
        })

    # 4. Final Payload
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "msteams": {"width": "Full"},
                    "body": card_body
                }
            }
        ]
    }

    try:
        response = manager.session.post(TEAMS_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code not in [200, 202]:
            print(f"❌ Teams Hatası: {response.status_code}")
    except Exception as e:
        print(f"❌ Teams Bağlantı Hatası: {e}")

def analizi_yap(xml_response, draft_name, limit_mile, target_warehouses_str):

    """
    Returns:
    - True: Opportunity found (Copy)
    - False: Continue waiting
    - "STOP": Bad keyword found (Remove from list)
    """

    manager.add_log("📊 Sonuçlar analiz ediliyor...")
    
    html_parts = re.findall(r'<!\[CDATA\[(.*?)]]>', xml_response, re.DOTALL)
    full_html = "".join(html_parts)
    soup = BeautifulSoup(full_html, 'html.parser')
    
    plans_table = soup.find("tbody", id=lambda x: x and "plans" in x)
    if not plans_table: return False

    rows = plans_table.find_all("tr")
    current_option = "Bilinmiyor"

    target_list = [t.strip().upper() for t in target_warehouses_str.split(',') if t.strip()]
    
    bulunan_firsatlar = {} # Dictionary to store merged results
    firsat_sayisi = 0

    for row in rows:
        if "ui-rowgroup-header" in row.get("class", []):
            current_option = row.get_text(strip=True)
            continue
            
        cells = row.find_all("td")
        if len(cells) > 3:
            dist_text = cells[3].get_text(strip=True)
            if "mi" in dist_text:
                try:
                    mil = int(dist_text.replace("mi", "").replace(",", "").strip())
                    dest = cells[2].get_text(strip=True).upper()
                    
                    if "Amazon Optimized" in current_option: continue
                    
                    # --- PRIORITY 1: TARGET WAREHOUSE (STOP CONDITION) ---
                    if any(target in dest for target in target_list):
                        manager.add_log(f"🎯 HEDEF DEPO BULUNDU! ({dest}) - Takip Bitiyor.", "success")
                        teams_bildirim_gonder(
                            title="🎯 Hedef Depo Yakalandı!",
                            message=f"**{draft_name}** için hedef depo (**{dest}**) bulundu. Takip listesinden çıkarılıyor.",
                            status="success",
                            facts={"Depo": dest, "Mesafe": f"{mil} Mil", "Plan": current_option}
                        )
                        return "FOUND_TARGET" # Special signal to STOP
                    
                    # --- PRIORITY 2: MILE LIMIT (COPY CONDITION) ---
                    elif mil < limit_mile:
                        manager.add_log(f"✅ MESAFE UYGUN: {mil} Mil ({dest})", "success")
                        firsat_sayisi += 1
                        bulunan_firsatlar[current_option] = f"{mil} Mil ➡️ {dest}"

                except: pass

    # --- SEND SINGLE NOTIFICATION ---
    if bulunan_firsatlar:
        teams_bildirim_gonder(
            title=f"{firsat_sayisi} Adet Fırsat Bulundu!",
            message=f"**{draft_name}** için aşağıdaki planlar kriterlerinize ({manager.mile_threshold} mil altı) uyuyor:",
            status="success",
            facts=bulunan_firsatlar # Passes the dictionary we built
        )
        return True # Return True so the bot knows to proceed with Copying

    return False

def poll_results_until_complete(session, base_payload, referer_url):
    max_retries = 60
    last_percent = 0

    for i in range(max_retries):
        try:
            poll_params = {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": "mainForm:planingStatusDialogPoll",
                "javax.faces.partial.execute": "@all",
                "javax.faces.partial.render": "mainForm:shipmentPlansPanel mainForm:a2dw_boxContentPanel mainForm:progressBarPlaning",
                "mainForm:planingStatusDialogPoll": "mainForm:planingStatusDialogPoll",
                "mainForm": "mainForm"
            }
            res = session.post(PLAN_URL, data={**base_payload, **poll_params}, headers={"Referer": referer_url})
            
            if "javax.faces.ViewState" in res.text:
                try:
                    match = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res.text)
                    if match: base_payload["javax.faces.ViewState"] = match.group(1)
                except: pass

            #if "mainForm:plans" in res.text or "Amazon Optimized Splits" in res.text:
                #return res.text
            
            match_percent = re.search(r'>\s*(\d+)\s*%\s*<', res.text)
            current_percent = int(match_percent.group(1)) if match_percent else 0

            if current_percent == 0 and last_percent > 50: return res.text
            if current_percent > last_percent: last_percent = current_percent
            time.sleep(5)
        except: time.sleep(5)
    return None

def drafti_kopyala(target_date):
    """
    Kopyalama yapar ve YENİ OLUŞAN DRAFT'IN ADINI döndürür.
    """
    manager.add_log("Kopyalama işlemi başlatılıyor...", "info")
    
    # 1. Target'dan draftı bul
    res = manager.session.get(DRAFT_PAGE_URL)
    if "login.jsf" in res.url: login(); res = manager.session.get(DRAFT_PAGE_URL)
    
    df = html_tabloyu_parse_et(res.text)
    if df.empty: return None

    ilgili_satir = df[df["Created"] == target_date]
    if ilgili_satir.empty: 
        manager.add_log("Kopyalanacak satır tarihle bulunamadı.", "error")
        return None
    
    copy_id = ilgili_satir.iloc[0]["Copy ID"]
    base_loc = str(ilgili_satir.iloc[0]["From"])
    if not copy_id: return None
        
    # 2. Copy Butonuna Bas
    form_data = form_verilerini_topla(res.text)
    copy_payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": copy_id,
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": "clone_draft_confirm",
        copy_id: copy_id,
        "mainForm": "mainForm"
    }
    res_confirm = manager.session.post(DRAFT_PAGE_URL, data={**form_data, **copy_payload})
    
    # 3. Confirm (Yes) Butonuna Bas
    confirm_btn_id = None
    try:
        match = re.search(r'button id="([^"]+)"[^>]*class="[^"]*ui-confirmdialog-yes', res_confirm.text)
        if match: confirm_btn_id = match.group(1)
    except: pass
    
    if not confirm_btn_id: return None
        
    current_vs = form_data.get("javax.faces.ViewState")
    try:
        match_vs = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res_confirm.text)
        if match_vs: current_vs = match_vs.group(1)
    except: pass

    confirm_payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": confirm_btn_id,
        "javax.faces.partial.execute": "@all",
        confirm_btn_id: confirm_btn_id,
        "mainForm": "mainForm",
        "javax.faces.ViewState": current_vs
    }
    
    res_final = manager.session.post(DRAFT_PAGE_URL, data=confirm_payload)

    # 4. Redirect ve Yeni İsim Alma
    if "<redirect" in res_final.text:
        try:
            redirect_part = res_final.text.split('url="')[1].split('"')[0].replace("&amp;", "&")
            full_redirect_url = urllib.parse.urljoin(BASE_URL, redirect_part)
            
            # Yeni sayfaya git
            new_page_res = manager.session.get(full_redirect_url)    
            soup_new = BeautifulSoup(new_page_res.text, 'html.parser')

            name_input = soup_new.find("input", {"name": lambda x: x and "draft_name" in x})
            new_draft_name = name_input.get("value") if name_input else "Bilinmeyen Kopya"

            loc_span = soup_new.find("span", {"id": "mainForm:draftInfo:0:ship_from_address"})
            new_location = loc_span.get_text(strip=True) if loc_span else ""

            manager.add_log(f"✅ Kopyalandı: {new_draft_name}")
            
            if base_loc.lower() not in new_location.lower():
                manager.add_log(f"📍 Adres düzeltiliyor: {new_location} -> {base_loc}", "warning")
                address_request_handler(full_redirect_url, target_date, new_page_res)
            
            time.sleep(2) # Sistemin oturması için
            res_check = manager.session.get(DRAFT_PAGE_URL)
            soup_list = BeautifulSoup(res_check.text, 'html.parser')
            df_check = html_tabloyu_parse_et(res_check.text)
            yeni_satir = df_check[df_check["Draft Name"] == new_draft_name]

            if not yeni_satir.empty:
                yeni_tarih = yeni_satir.iloc[0]["Created"]
                loc = yeni_satir.iloc[0]["From"]
                new_input_id = yeni_satir.iloc[0]["Name Input ID"]
                clean_base = re.sub(r'(\s*-\s*copy|\s*copy|\s*-\s*clone)+', '', new_draft_name, flags=re.IGNORECASE).strip()
                # Eski tarihleri temizle
                clean_base = re.sub(r'\s\d{2}[/.-]\d{2}\s\d{2}:\d{2}:\d{2}$', '', clean_base)
                
                # Yeni Tarih Ekle (Gün/Ay Saat:Dk:Sn)
                unique_ts = datetime.now().strftime("%d/%m %H:%M:%S")
                if len(clean_base) > 30: clean_base = clean_base[:30]
                new_clean_name = f"{clean_base} {unique_ts}"
                
                # ViewState'i formdan al
                vs_input = soup_list.find("input", {"name": "javax.faces.ViewState"})
                current_vs = vs_input.get("value")
                
                # --- RENAME SEQUENCE ÇAĞIR ---
                if rename_draft_sequence(new_input_id, new_clean_name, soup_list, current_vs):
                    final_draft_name = new_clean_name
                    manager.add_log(f"✏️ İsim düzeltildi: {new_clean_name}")
                else:
                    final_draft_name = new_draft_name
                
                # SUCCESS NOTIFICATION
                # teams_bildirim_gonder(
                #     title="Kopyalama Başarılı",
                #     message="Yeni taslak oluşturuldu ve takip listesine eklendi.",
                #     status="info",
                #     facts={
                #         "Eski Taslak": str(target_date), # Or original name if you pass it
                #         "Yeni Taslak": new_draft_name,
                #         "Lokasyon": loc,
                #         "Tarih": yeni_tarih
                #     }
                # )
                time.sleep(2) # Sistemin oturması için
                res_final_check = manager.session.get(DRAFT_PAGE_URL)
                df_check = html_tabloyu_parse_et(res_final_check.text)
                yeni_satir = df_check[df_check["Draft Name"] == final_draft_name]

                if not yeni_satir.empty:
                    yeni_tarih = yeni_satir.iloc[0]["Created"]
                    loc = yeni_satir.iloc[0]["From"]
                return {"name": final_draft_name, "date": yeni_tarih, "loc": loc}
            else:
                manager.add_log("⚠️ Kopyalanan satır listede bulunamadı (Rename atlandı).", "warning")
            return None
            
        except Exception as e: 
            print(f"Kopya isim hatası: {e}")
            return None
            
    return None

def drafti_planla_backend(target_date, draft_name, loc, limit_mile, target_warehouses):
    try:
        # 1. Draft Aç
        manager.add_log(f"İşlem başladı: {draft_name}", "info")
        main_res = manager.session.get(DRAFT_PAGE_URL)
        if "login.jsf" in main_res.url: login(); main_res = manager.session.get(DRAFT_PAGE_URL)

        df = html_tabloyu_parse_et(main_res.text)
        target_row = df[df["Created"] == target_date]

        if target_row.empty:
            manager.add_log(f"⚠️ {draft_name} listede bulunamadı! (Tarih eşleşmedi)", "warning")
            return None
        current_action_id = target_row.iloc[0]["Action ID"]

        form_data = form_verilerini_topla(main_res.text)
        action_payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": current_action_id,
            "javax.faces.partial.execute": "@all",
            current_action_id: current_action_id, 
            "mainForm": "mainForm"
        }
        res_open = manager.session.post(DRAFT_PAGE_URL, data={**form_data, **action_payload})
        
        # Redirect Check
        redirect_url = None
        if "<redirect" in res_open.text:
            try:
                redirect_part = res_open.text.split('url="')[1].split('"')[0].replace("&amp;", "&")
                redirect_url = urllib.parse.urljoin(BASE_URL, redirect_part)
            except: pass
        
        if not redirect_url:
            manager.add_log(f"{draft_name} açılamadı.", "error")
            return None # Return None = Kopyalama olmadı

        manager.session.get(redirect_url) # Detay sayfası
        
        # 2. Planlama
        manager.add_log("🚀 Planlama başlatılıyor...")
        detay_res = manager.session.get(redirect_url)
        detay_form_data = form_verilerini_topla(detay_res.text)
        create_plan_params = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "mainForm:create_plan",
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "mainForm",
            "mainForm:create_plan": "mainForm:create_plan",
            "mainForm": "mainForm"
        }
        res_plan = manager.session.post(PLAN_URL, data={**detay_form_data, **create_plan_params}, headers={"Referer": redirect_url})
        
        if "ui-messages-error" in res_plan.text:
             manager.add_log("Planlama hatası.", "error")
             return None

        # 3. Polling
        if "javax.faces.ViewState" in res_plan.text:
            try:
                 match = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res_plan.text)
                 if match: detay_form_data["javax.faces.ViewState"] = match.group(1)
            except: pass

        final_xml = final_xml = poll_results_until_complete(
            manager.session, 
            detay_form_data, 
            redirect_url, 
        )
        
        if final_xml:
            sonuc = analizi_yap(final_xml, draft_name, limit_mile, target_warehouses)
            if sonuc == "FOUND_TARGET":
                manager.add_log(f"🏁 {draft_name}: Hedef depo bulunduğu için işlem sonlandırıldı.", "success")
                return "STOP" # This removes it from the watchlist
            
            elif sonuc is True:
                # Kopyala ve yeni ismi döndür
                yeni_draft_verisi = drafti_kopyala(target_date)
                if yeni_draft_verisi:
                    manager.add_log(f"✅ {draft_name} süreci tamamlandı. Yeni: {yeni_draft_verisi['name']}", "success")
                    
                    yeni_draft_verisi['max_mile'] = limit_mile
                    yeni_draft_verisi['targets'] = target_warehouses
                    manager.add_log(f"🔄 {draft_name} kopyalandı. Yeni takip: {yeni_draft_verisi['name']}", "success")
                    return yeni_draft_verisi
            
            manager.add_log(f"{draft_name} tamamlandı, fırsat yok.", "warning")
            return None
            
        return None

    except Exception as e:
        manager.add_log(f"Hata ({draft_name}): {str(e)}", "error")
        return None

def address_request_handler(draft_url, target_date, res_draft):

    # Get location:
    watch_df = manager.get_watch_list_df()
    filtered_row = watch_df[watch_df['date'] == target_date]
    location_value = None
    if not filtered_row.empty:
        # 3. Extract the value. You MUST select the 0th index because it is still a list-like object.
        location_value = filtered_row.iloc[0]["loc"] 
        print(location_value)
    else:
        print("No row found.")
        return None
    
    # Request the draft page:

    # res_draft = manager.session.get(draft_url)
    form_data = form_verilerini_topla(res_draft.text)
    current_viewstate = form_data.get("javax.faces.ViewState")
    draft_soup = BeautifulSoup(res_draft.text, "html.parser")

    # find the id of secret button
    # STRICT SEARCH: Find the script tag containing the specific function name
    # We use re.compile to match the content partially
    secret_btn_id = ""
    target_script = draft_soup.find('script', string=re.compile(r'updateAddress\s*='))

    if target_script and target_script.has_attr('id'):
        found_id = target_script['id']
        print(f"Found ID: {found_id}")
        secret_btn_id = found_id
    else:
        print("Target script not found or has no ID.")
    # Find pencil:

    edit_link = draft_soup.find("a", title="Change 'Ship From' address")
    if not edit_link: edit_link = draft_soup.find("a", id=re.compile(r"ship_from_address_edit"))
    if not edit_link:
        pencil_icon = draft_soup.find("i", class_="pi-pencil")
        if pencil_icon: edit_link = pencil_icon.find_parent("a")

    if not edit_link:
        manager.add_log("❌ Kalem butonu bulunamadı.", "error")
        return False

    edit_btn_id = edit_link.get("id")
        
    # Open modal

    payload_open = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": edit_btn_id,
        "javax.faces.partial.execute": edit_btn_id,
        "javax.faces.partial.render": "addressDialog:addressForm:addressTable", 
        edit_btn_id: edit_btn_id,
        "mainForm": "mainForm",
        **form_data 
    }
    data_rk = ""
    select_btn_id = ""
    xml_data = manager.session.post(PLAN_URL, data=payload_open)
    match_vs = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', xml_data.text)
    if match_vs: current_viewstate = match_vs.group(1)

    outer_soup = BeautifulSoup(xml_data.text, 'xml')

    update_tag = outer_soup.find('update', {'id': 'addressDialog:addressForm:addressTable'})

    if update_tag:
        inner_html_content = update_tag.text
        inner_soup = BeautifulSoup(inner_html_content, 'html.parser')

        # Find select button
        
        select_span = inner_soup.find('span', string='Select')
        if select_span:
            # 2. Go up to the parent button
            select_button = select_span.find_parent('button')
            # 3. (Optional) Get the ID to use later
            print(select_button['id'])
            select_btn_id = select_button["id"]
        else:
            print("cant find select buton")
            return None

        target_input = inner_soup.find('input', {'value': location_value})
        
        if target_input:
            parent_tr = target_input.find_parent('tr')
            
            if parent_tr and parent_tr.has_attr('data-rk'):
                print(f"FOUND MATCH!")
                print(f"Row Key (data-rk): {parent_tr['data-rk']}")
                data_rk = parent_tr['data-rk']
                modal_inputs = form_verilerini_topla(inner_html_content)
                payload_select = {
                    "javax.faces.partial.ajax": "true",
                    "javax.faces.source": select_btn_id,
                    "javax.faces.partial.execute": "addressDialog:addressForm", 
                    select_btn_id: select_btn_id,
                    "addressDialog:addressForm": "addressDialog:addressForm", 
                    "addressDialog:addressForm:addressTable_radio": "on", 
                    "addressDialog:addressForm:addressTable_selection": data_rk,
                    "javax.faces.ViewState": current_viewstate,
                    **modal_inputs 
                }
                res_select = manager.session.post(PLAN_URL, data=payload_select)
                if res_select.status_code == 200:
                    match_vs_2 = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res_select.text)
                    if match_vs_2: current_viewstate = match_vs_2.group(1)

                    modal_form_data = form_verilerini_topla(inner_html_content)

                    payload_refresh = {
                        "javax.faces.partial.ajax": "true",
                        "javax.faces.source": secret_btn_id,
                        "javax.faces.partial.execute": "@all",
                        "javax.faces.partial.render": "mainForm:draftInfo",
                        secret_btn_id: secret_btn_id,
                        "mainForm": "mainForm",
                        "javax.faces.ViewState": current_viewstate,
                        **modal_form_data
                    }
                    manager.session.post(PLAN_URL, data=payload_refresh)


            else:
                print("Found input, but parent TR has no data-rk.")
        else:
            print(f"Could not find input with value: {location_value}")

    else:
        print("Could not find the update tag with the table ID.")
    
def rename_draft_sequence(target_input_id, new_name, soup_page, current_vs):
    """
    Executes the 2-step rename sequence:
    1. Full Table Update (Request 1)
    2. Specific Change Event (Request 2)
    """
    print(f"🔄 Renaming sequence started for: {new_name}")

    # --- STEP 1: PREPARE PAYLOAD FOR REQUEST #1 (FULL TABLE) ---
    form = soup_page.find("form", id="mainForm")
    if not form: return False

    # Scrape ALL inputs to mimic the browser's full table submission
    payload_req1 = {}
    for tag in form.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        value = tag.get("value", "")
        if not name: continue
        if tag.get("type") in ["checkbox", "radio"] and not tag.has_attr("checked"):
            continue
        payload_req1[name] = value

    # Overwrite the specific target input with the NEW name
    payload_req1[target_input_id] = new_name
    
    # Add JSF Table Parameters (From your Request 1)
    payload_req1.update({
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": "mainForm:drafts", # Table ID
        "javax.faces.partial.execute": "mainForm:drafts",
        "javax.faces.partial.render": "mainForm:drafts",
        "mainForm:drafts": "mainForm:drafts",
        "mainForm:drafts_encodeFeature": "true",
        "javax.faces.ViewState": current_vs
    })

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Faces-Request": "partial/ajax",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": DRAFT_PAGE_URL
    }

    try:
        # --- SEND REQUEST #1 ---
        res1 = manager.session.post(DRAFT_PAGE_URL, data=payload_req1, headers=headers)
        
        if res1.status_code != 200:
            print(f"❌ Request 1 Failed: {res1.status_code}")
            return False

        # IMPORTANT: Capture the NEW ViewState from Request 1 to use in Request 2
        # JSF updates the state after every AJAX request.
        match_vs = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res1.text)
        next_viewstate = match_vs.group(1) if match_vs else current_vs
        
        # --- STEP 2: PREPARE PAYLOAD FOR REQUEST #2 (CHANGE EVENT) ---
        payload_req2 = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": target_input_id,
            "javax.faces.partial.execute": target_input_id,
            "javax.faces.behavior.event": "change",
            "javax.faces.partial.event": "change",
            "javax.faces.partial.render": "@none", # Assuming we don't need re-render
            target_input_id: new_name, # The Key must be the Input ID
            "mainForm": "mainForm",
            "javax.faces.ViewState": next_viewstate # Use the FRESH ViewState
        }

        # --- SEND REQUEST #2 ---
        res2 = manager.session.post(DRAFT_PAGE_URL, data=payload_req2, headers=headers)
        
        if res2.status_code == 200:
            print(f"✅ Rename Sequence Complete: {new_name}")
            return True
        else:
            print(f"❌ Request 2 Failed: {res2.status_code}")
            return False

    except Exception as e:
        print(f"❌ Rename Sequence Error: {e}")
        return False

def gorev():
    if not manager.is_running:
        # Optional: Print to console for debug, but don't spam UI logs
        print("Bot duraklatıldı. Görev atlanıyor.")
        return
    current_list = manager.watch_list
    if not current_list: return

    manager.add_log(f"⏰ Periyodik kontrol başladı. ({len(current_list)} adet)", "info")
    
    indices_to_remove = []
    indices_to_update = {} 

    # 1. GROUP BY ACCOUNT ID
    # This prevents switching A->B->A->B. It does A, A, A -> Switch -> B, B.
    # We sort the list by account_id to group them.
    # Note: We keep the original index 'i' to manage updates/deletions correctly.
    indexed_list = list(enumerate(current_list))
    sorted_tasks = sorted(indexed_list, key=lambda x: x[1].get('account_id', ''))

    for i, item in sorted_tasks:
        d_name = item['name']
        d_date = item['date']
        d_loc = item['loc']
        target_acc_id = item.get('account_id')
        target_acc_name = item.get('account_name', 'Bilinmiyor')

        # --- CONTEXT SWITCHING LOGIC ---
        if target_acc_id and target_acc_id != manager.current_account_id:
            manager.add_log(f"🔄 Hesap Değiştiriliyor: {target_acc_name}...", "warning")
            success = switch_account_backend(target_acc_id)
            if success:
                manager.current_account_id = target_acc_id # Update state locally
                manager.current_account_name = target_acc_name
                time.sleep(2) # Wait for session to settle
            else:
                manager.add_log(f"❌ Hesap geçişi başarısız: {d_name} atlanıyor.", "error")
                continue # Skip this task if we can't switch

        # --- PROCESS (Now we are in the correct account) ---
        d_limit = item.get('max_mile', manager.mile_threshold)
        d_targets = item.get('targets', "") 
        
        sonuc = drafti_planla_backend(d_date, d_name, d_loc, d_limit, d_targets)
        
        if sonuc == "STOP":
            indices_to_remove.append(i)
        elif isinstance(sonuc, dict):
            # Important: Carry over the Account ID to the new copy
            sonuc['account_id'] = target_acc_id
            sonuc['account_name'] = target_acc_name
            indices_to_update[i] = sonuc

    # --- REBUILD LIST ---
    if indices_to_remove or indices_to_update:
        new_watch_list = []
        for i, item in enumerate(current_list):
            if i in indices_to_remove: continue
            if i in indices_to_update:
                new_watch_list.append(indices_to_update[i])
            else:
                new_watch_list.append(item)
        
        manager.update_watch_list(new_watch_list)
        print("Global manager listesi güncellendi.")

# --- SCHEDULER BAŞLATMA ---
@st.cache_resource
def start_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(gorev, 'interval', minutes=manager.mins_threshold, id='ana_gorev', max_instances=1, misfire_grace_time=None)
    sched.start()
    return sched

scheduler = start_scheduler()

# --- UI TASARIMI ---
st.set_page_config(page_title="Kargo Paneli", layout="wide")

# --- SIDEBAR SETTINGS ---
with st.sidebar:
    st.header("⚙️ Bot Ayarları")
    
    # Mil Ayarı
    mile_limit = st.number_input(
        "Fırsat Mil Sınırı (Mil)", 
        min_value=0, 
        max_value=5000, 
        value=manager.mile_threshold, 
        step=50,
        help="Planlanan kargo bu mesafenin altındaysa otomatik kopya oluşturulur."
    )
    
    # Update Manager if changed
    if mile_limit != manager.mile_threshold:
        manager.set_mile_threshold(mile_limit)
        st.toast(f"✅ Sınır güncellendi: {mile_limit} Mil")

    # Min Ayarı
    min_limit = st.number_input(
        "Tekrar deneme dakikası", 
        min_value=1, 
        max_value=500, 
        value=manager.mins_threshold, 
        step=5,
        help="Botun kaç dakikada bir kontrol edeceğini belirler."
    )
    
    # Update Manager and Reschedule Job if changed
    if min_limit != manager.mins_threshold:
        manager.set_mins_threshold(min_limit)
        
        try:
            scheduler.reschedule_job('ana_gorev', trigger='interval', minutes=min_limit)
            st.toast(f"✅ Sıklık güncellendi: {min_limit} dakikada bir çalışacak.")
            manager.add_log(f"Zamanlayıcı güncellendi: Yeni aralık {min_limit} dk.", "warning")
        except Exception as e:
            st.error(f"Zamanlayıcı güncellenemedi (Bot çalışmıyor olabilir): {e}")
        
    st.divider()
    st.caption(f"Aktif Mil Sınır: **{manager.mile_threshold} Mil**")
    st.caption(f"Aktif Dakika Sınır: **{manager.mins_threshold} Dakika**")

st.title("📑 Otomatik Kargo Botu")
st.divider()

# 2. BÖLÜM: TASLAK SEÇİMİ (MEVCUT LİSTE)
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📦 Mevcut Taslaklar")

    header_col, menu_col = st.columns([3, 0.75], gap="small")

    with header_col:
        if st.button("🔄 Taslakları Yenile"):
            st.cache_data.clear()
            st.rerun()
    with menu_col:
        # Seçili olanı göster
        current_name = manager.current_account_name
        label = f"🏢 {current_name}"
        
        # Popover (Açılır Menü)
        with st.popover(label, use_container_width=True):
            st.caption("Hesap Değiştir")
            
            # DURUM 1: Henüz hesaplar çekilmediyse "Getir" butonu göster
            if not manager.available_accounts:
                with st.spinner("Hesaplar çekiliyor..."):
                        if not manager.session.cookies: 
                            login()
                        
                        fetch_success = fetch_accounts_backend()
                        
                        if fetch_success:
                            st.success("Listelendi!")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error("Çekilemedi.")
                # FIX: Logic is now INSIDE the button check
                if st.button("Hesapları Getir", key="fetch_acc_btn", use_container_width=True):
                    with st.spinner("Hesaplar çekiliyor..."):
                        if not manager.session.cookies: 
                            login()
                        
                        fetch_success = fetch_accounts_backend()
                        
                        if fetch_success:
                            st.success("Listelendi!")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error("Çekilemedi.")

            # DURUM 2: Hesaplar varsa onları listele
            else:
                for acc in manager.available_accounts:
                    is_selected = acc.get('is_active', False)
                    btn_style = "primary" if is_selected else "secondary"
                    flag = acc.get('flag', '🇺🇸')
                    name_label = f"{flag} {acc['name']}"
                    
                    if st.button(name_label, 
                                key=f"btn_switch_{acc['id']}", 
                                type=btn_style, 
                                disabled=is_selected, 
                                use_container_width=True):
                        
                        with st.spinner(f"{acc['name']} hesabına geçiliyor..."):
                            success = switch_account_backend(acc['id'])
                            if success:
                                st.success("Geçiş yapıldı!")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("Geçiş başarısız.")
    df, hata = veriyi_dataframe_yap()
    
    if df is not None and not df.empty:
        grid_response = st.data_editor(
            df,
            column_config={
                "Seç": st.column_config.CheckboxColumn("Ekle", default=False),
                "Max Mil": st.column_config.NumberColumn("Max Mil", step=50, help="Bu taslak için özel mil sınırı"),
                "Hedef Depolar": st.column_config.TextColumn("Hedef Depolar", help="Örn: AVP1, TEB3 (Virgülle ayırın)"),
                "Action ID": None,
                "Copy ID": None
            },
            disabled=["Draft Name", "From", "Created", "Name Input ID"],
            hide_index=True,
            width='stretch',
            key="draft_selector"
        )
        
        secili_satirlar = grid_response[grid_response["Seç"] == True]
        
        if st.button(f"➕ Seçili {len(secili_satirlar)} Taslağı Takibe Ekle"):
            current = manager.watch_list
            mevcut_tarihler = {item['date'] for item in current if 'date' in item}
            
            # GUARD: Ensure we know the current account
            if not manager.current_account_id:
                st.error("⚠️ Aktif hesap ID'si bulunamadı. Lütfen önce 'Hesapları Getir' butonuna basın.")
            else:
                eklenen_sayisi = 0
                for index, row in secili_satirlar.iterrows():
                    new_date = row['Created']
                    
                    if new_date not in mevcut_tarihler:
                        current.append({
                            'account_id': manager.current_account_id,   # <--- SAVE ID
                            'account_name': manager.current_account_name, # <--- SAVE NAME (Visual)
                            'name': row['Draft Name'], 
                            'date': new_date, 
                            'loc': row["From"],
                            'max_mile': int(row["Max Mil"]),
                            'targets': str(row["Hedef Depolar"])
                        })
                        mevcut_tarihler.add(new_date)
                        eklenen_sayisi += 1
            
                if eklenen_sayisi > 0:
                    manager.update_watch_list(current)

                    st.success(f"{eklenen_sayisi} yeni taslak eklendi ve işlem sıraya alındı!")
                    time.sleep(1) # Kullanıcı mesajı okusun
                    st.rerun()
                else:
                    st.warning("Seçilenlerin hepsi zaten takip listesinde mevcut.")

# 3. BÖLÜM: CANLI LOGLAR (SAĞ PANEL)
with col2:
    st.subheader("📡 Canlı Loglar")
    
    # Logları otomatik yenilemek için basit bir döngü yerine buton veya fragment
    # Streamlit 1.37+ kullanıyorsan st.fragment süper olur, yoksa manuel yenileme butonu
    
    if st.button("Logları Yenile"):
        pass # Sadece rerun tetikler
    
    log_container = st.container(height=400)
    with log_container:
        for log in manager.logs:
            st.text(log)
            
    # Otomatik yenileme notu
    st.caption("Loglar arka planda birikir. Sayfayı yenileyerek veya butona basarak görebilirsiniz.")

st.divider()

# 1. BÖLÜM: TAKİP LİSTESİ YÖNETİMİ
# We create a layout: [Header Text] --- [Status Text] --- [Start Btn] [Stop Btn]
list_header_col, status_col, controls_col = st.columns([4, 2, 2], gap="small", vertical_alignment="center")

with list_header_col:
    st.subheader("📋 Aktif Takip Listesi")

with status_col:
    # Status Indicator aligned to the right of the text
    if manager.is_running:
        st.markdown("**:green[● ÇALIŞIYOR]**", help=f"Bot aktif. {manager.mins_threshold} dakikada bir kontrol ediliyor.")
    else:
        st.markdown("**:red[● DURDURULDU]**", help="Bot şu an işlem yapmıyor.")

with controls_col:
    # Nested columns for tight button spacing
    start_btn_col, stop_btn_col = st.columns(2)
    
    with start_btn_col:
        # Start Button
        if st.button("BAŞLAT", help="Botu Başlat", type="secondary", use_container_width=True, disabled=manager.is_running, ):
            manager.is_running = True
            manager.add_log("▶️ Bot başlatıldı.", "success")
            try:
                # Trigger immediate run
                scheduler.add_job(gorev, 'date', run_date=datetime.now())
                st.toast("Bot başlatıldı, ilk kontrol yapılıyor...")
            except: pass
            st.rerun()

    with stop_btn_col:
        # Stop Button
        if st.button("DURDUR", help="Botu Durdur", type="secondary", use_container_width=True, disabled=not manager.is_running):
            manager.is_running = False
            manager.add_log("⏹️ Bot durduruldu.", "warning")
            st.toast("Bot durduruldu.")
            st.rerun()

# --- DATAFRAME EDITOR ---
watch_df = manager.get_watch_list_df()

if not watch_df.empty:
    edited_watch_df = st.data_editor(
        watch_df,
        column_config={
            "account_name": "Hesap",
            "name": "Taslak Adı",
            "date": "Created",
            "loc": "From",
            "max_mile": st.column_config.NumberColumn("Limit", step=50, help="Bu taslak için özel mil sınırı"),
            "targets": st.column_config.TextColumn("Hedefler", help="Örn: AVP1, TEB3")
        },
        disabled=["account_name", "name", "date", "loc"],
        num_rows="dynamic",
        key="watch_list_editor",
        width='stretch'
    )
    
    if st.button("💾 Değişiklikleri Kaydet", use_container_width=True):
        yeni_liste_dict = edited_watch_df.to_dict("records")
        manager.update_watch_list(yeni_liste_dict)
        st.success("Takip listesi güncellendi!")
        st.rerun()
else:
    st.info("Takip listesi şu an boş. Yukarıdan taslak seçip ekleyin.")

