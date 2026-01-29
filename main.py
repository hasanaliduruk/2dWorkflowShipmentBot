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

        # --- CRITICAL FIX: Session managed here, not in st.session_state ---
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self.available_accounts = [] 
        self.current_account_name = "Can't Find!"
        
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
        return (df, None) if not df.empty else (None, "Tablo boş.")
    except Exception as e: return None, str(e)

def teams_bildirim_gonder(title, message, facts=None, status="info"):
    """
    Sends a consolidated Adaptive Card to Teams.
    """
    # 1. Color Mapping
    color_map = {"success": "Good", "error": "Attention", "warning": "Warning", "info": "Accent"}
    theme_color = color_map.get(status, "Accent")
    
    # 2. Prepare FactSet (The Table)
    fact_items = []
    if facts:
        for k, v in facts.items():
            fact_items.append({"title": k, "value": str(v)})

    # 3. Construct Payload
    card_body = [
        {
            "type": "Container",
            "style": theme_color,
            "bleed": True,
            "items": [
                {
                    "type": "TextBlock",
                    "text": f"{'✅' if status=='success' else 'ℹ️'} {title}",
                    "weight": "Bolder",
                    "size": "Medium",
                    "color": "Light" if status in ["error", "info"] else "Dark"
                }
            ]
        },
        {
            "type": "Container",
            "items": [
                {
                    "type": "TextBlock",
                    "text": message,
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "Small"
                }
            ]
        }
    ]

    # Add the table if we have facts
    if fact_items:
        card_body[1]["items"].append({
            "type": "FactSet",
            "facts": fact_items,
            "separator": True,
            "spacing": "Medium"
        })

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "msteams": {"width": "Full"},
                "body": card_body
            }
        }]
    }

    try:
        manager.session.post(TEAMS_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Teams Error: {e}")

def analizi_yap(xml_response, draft_name):
    manager.add_log("📊 Sonuçlar analiz ediliyor...")
    
    html_parts = re.findall(r'<!\[CDATA\[(.*?)]]>', xml_response, re.DOTALL)
    full_html = "".join(html_parts)
    soup = BeautifulSoup(full_html, 'html.parser')
    
    plans_table = soup.find("tbody", id=lambda x: x and "plans" in x)
    if not plans_table: return False

    rows = plans_table.find_all("tr")
    current_option = "Bilinmiyor"
    firsat_bulundu = False
    msg = "=============" + draft_name + "=============\n\n"
    
    bulunan_firsatlar = {} # Dictionary to store merged results
    firsat_sayisi = 0

    for row in rows:
        # Check if it's a Header Row (e.g., "Shipping Option 1")
        if "ui-rowgroup-header" in row.get("class", []):
            current_option = row.get_text(strip=True)
            continue
            
        cells = row.find_all("td")
        if len(cells) > 3:
            dist_text = cells[3].get_text(strip=True)
            if "mi" in dist_text:
                try:
                    mil = int(dist_text.replace("mi", "").replace(",", "").strip())
                    dest = cells[2].get_text(strip=True)
                    
                    if "Amazon Optimized" in current_option: continue
                    
                    if mil < manager.mile_threshold:
                        # LOGGING (Keep internal logs for each find)
                        manager.add_log(f"✅ FIRSAT: {mil} Mil ({dest}) - {current_option}", "success")
                        
                        # COLLECT DATA
                        # Key = Plan Name, Value = Details
                        bulunan_firsatlar[current_option] = f"{mil} Mil ➡️ {dest}"
                        firsat_sayisi += 1
                    else:
                        manager.add_log(f"❌ {mil} Mil ({dest}) - Uygun değil")
                except: pass

    # --- SEND SINGLE NOTIFICATION ---
    if firsat_sayisi > 0:
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
            
            time.sleep(1.5) # Sistemin oturması için
            res_check = manager.session.get(DRAFT_PAGE_URL)
            df_check = html_tabloyu_parse_et(res_check.text)
            yeni_satir = df_check[df_check["Draft Name"] == new_draft_name]

            if not yeni_satir.empty:
                yeni_tarih = yeni_satir.iloc[0]["Created"]
                loc = yeni_satir.iloc[0]["From"]
                
                # SUCCESS NOTIFICATION
                teams_bildirim_gonder(
                    title="Kopyalama Başarılı",
                    message="Yeni taslak oluşturuldu ve takip listesine eklendi.",
                    status="info",
                    facts={
                        "Eski Taslak": str(target_date), # Or original name if you pass it
                        "Yeni Taslak": new_draft_name,
                        "Lokasyon": loc,
                        "Tarih": yeni_tarih
                    }
                )

                return {"name": new_draft_name, "date": yeni_tarih, "loc": loc}
            
            return None
            
        except Exception as e: 
            print(f"Kopya isim hatası: {e}")
            return None
            
    return None

def drafti_planla_backend(target_date, draft_name, loc):
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
            firsat_var_mi = analizi_yap(final_xml, draft_name)
            
            if firsat_var_mi:
                # Kopyala ve yeni ismi döndür
                yeni_draft_verisi = drafti_kopyala(target_date)
                if yeni_draft_verisi:
                    manager.add_log(f"✅ {draft_name} süreci tamamlandı. Yeni: {yeni_draft_verisi['name']}", "success")
                    
                    # --- KRİTİK: LİSTEYİ GÜNCELLE ---
                    # Otomatik görevde yeni kopyayı takip listesine ekle, eskisini çıkar
                    # Bu mantığı aşağıda `gorev` fonksiyonunda da yönetebiliriz ama buradan dönmek en temizi.
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
    



def gorev():
    current_list = manager.watch_list
    
    if not current_list:
        return

    manager.add_log(f"⏰ Periyodik kontrol başladı. ({len(current_list)} adet)", "info")
    
    # Listede değişiklik olursa kaydetmek için kopyasını al
    yeni_liste_guncellendi = False
    
    for i, item in enumerate(current_list):
        d_name = item['name']
        d_date = item['date']
        d_loc = item['loc']
        
        # Backend fonksiyonunu çağır (Artık dict dönüyor)
        yeni_draft_verisi = drafti_planla_backend(d_date, d_name, d_loc)
        
        if yeni_draft_verisi:
            # İşlem başarılı oldu ve yeni bir kopya oluştu
            # Listenin o sırasındaki elemanı YENİ VERİ ile değiştir
            manager.watch_list[i] = yeni_draft_verisi
            yeni_liste_guncellendi = True
            
            manager.add_log(f"🔄 Takip listesi güncellendi: {d_date} -> {yeni_draft_verisi['date']}", "success")
            
    if yeni_liste_guncellendi:
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
                "Action ID": None,
                "Copy ID": None
            },
            disabled=["Draft Name", "From", "Created"],
            hide_index=True,
            width='stretch',
            key="draft_selector"
        )
        
        secili_satirlar = grid_response[grid_response["Seç"] == True]
        
        if st.button(f"➕ Seçili {len(secili_satirlar)} Taslağı Takibe Ekle"):
            current = manager.watch_list
            
            # --- MÜKERRER KAYIT ENGELLEME EKLENDİ ---
            # Mevcut ID'leri hızlı kontrol için kümeye al
            mevcut_tarihler = {item['date'] for item in current if 'date' in item}
            
            eklenen_sayisi = 0
            for index, row in secili_satirlar.iterrows():
                new_date = row['Created']
                
                # Eğer listede yoksa ekle
                if new_date not in mevcut_tarihler:
                    current.append({'name': row['Draft Name'], 'date': new_date, 'loc': row["From"]})
                    mevcut_tarihler.add(new_date)
                    eklenen_sayisi += 1
            
            if eklenen_sayisi > 0:
                manager.update_watch_list(current)
                
                # --- KRİTİK EKLEME: HEMEN BAŞLAT ---
                # Scheduler'a "gorev" fonksiyonunu ŞU AN ('date' modunda) çalıştırmasını söylüyoruz.
                # Periyodik döngü bozulmaz, sadece araya bir işlem sıkıştırır.
                try:
                    scheduler.add_job(gorev, 'date', run_date=datetime.now())
                    st.toast("🚀 İşlem arka planda hemen başlatıldı!")
                except Exception as e:
                    st.warning(f"Otomatik başlatma tetiklenemedi (Zaten çalışıyor olabilir): {e}")

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
st.subheader("📋 Aktif Takip Listesi")
watch_df = manager.get_watch_list_df()

if not watch_df.empty:
    # Kullanıcıya silme imkanı veren editör
    edited_watch_df = st.data_editor(
        watch_df,
        column_config={
            "name": "Taslak Adı",
            "date": "Created",
            "loc": "From"
        },
        num_rows="dynamic", # Satır ekleme/silme açık
        key="watch_list_editor",
        width='stretch'
    )
    
    # Data editor'den gelen güncel veriyi manager'a kaydet
    # Sadece butonla kaydetmek daha güvenli (her harfte tetiklenmemesi için)
    if st.button("💾 Listeyi Güncelle"):
        yeni_liste_dict = edited_watch_df.to_dict("records")
        manager.update_watch_list(yeni_liste_dict)
        st.success("Takip listesi güncellendi!")
        st.rerun()
else:
    st.info("Takip listesi şu an boş. Aşağıdan taslak seçip ekleyin.")

