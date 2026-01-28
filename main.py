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
        
        self.mile_threshold = 300

        # --- CRITICAL FIX: Session managed here, not in st.session_state ---
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
    def set_mile_threshold(self, new_val):
        self.mile_threshold = new_val

    def add_log(self, message, type="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = "ℹ️"
        if type == "success": icon = "✅"
        elif type == "error": icon = "❌"
        elif type == "warning": icon = "⚠️"
        
        log_entry = f"{timestamp} {icon} {message}"
        self.logs.appendleft(log_entry)
        print(log_entry)

    def update_watch_list(self, new_list):
        self.watch_list = new_list

    def get_watch_list_df(self):
        return pd.DataFrame(self.watch_list)

@st.cache_resource
def get_manager():
    return GlobalManager()

manager = get_manager()

# --- HESAP SEÇİM AYARLARI ---
# Buradaki verileri kendi DB veya config dosyanızdan çekebilirsiniz.
ACCOUNTS = [
    {"id": "babil", "name": "Babil Design", "flag": "🇺🇸"},
    {"id": "kwiek", "name": "KWIEK-USA", "flag": "🇺🇸"},
]

# Varsayılan seçim yoksa ilkini seç
if "selected_account" not in st.session_state:
    st.session_state.selected_account = ACCOUNTS[0]

# --- KONFIGURASYON ---
try:
    TEAMS_WEBHOOK_URL = st.secrets["TEAMS_WEBHOOK"]
    USER_EMAIL = st.secrets["DB_EMAIL"]
    USER_PASS = st.secrets["DB_PASS"]
except:
    # Buraya kendi bilgilerini gir
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
        return True

    except Exception as e:
        print(f"Login işlem hatası: {e}")

        return False

def change_account(account):
    st.session_state.selected_account = account
    
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

def teams_bildirim_gonder(mesaj):
    payload = {
        "type": "AdaptiveCard",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "Kargo İşlem Raporu"},
            {"type": "TextBlock", "text": mesaj, "wrap": True}
        ],
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4"
    }
    try: manager.session.post(TEAMS_WEBHOOK_URL, json=payload, headers={'Content-Type': 'application/json'})
    except: pass

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
    
    limit = manager.mile_threshold

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
                    dest = cells[2].get_text(strip=True)
                    
                    if "Amazon Optimized" in current_option: continue
                    
                    if mil < limit:
                        detay = f"✅ FIRSAT! {mil} Mil - Plan: {current_option} - Depo: {dest}"
                        manager.add_log(detay, "success")
                        msg += detay + "\n"
                        firsat_bulundu = True
                    else:
                        detay = f"❌ {mil} Mil ({dest}) - Uygun değil"
                        msg += detay + "\n"
                        manager.add_log(detay)
                except: pass
    if msg: teams_bildirim_gonder(msg)
    return firsat_bulundu

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

def drafti_kopyala(target_date, original_from_loc):
    """
    Kopyalama yapar ve YENİ OLUŞAN DRAFT'IN ADINI döndürür.
    """
    manager.add_log("Kopyalama işlemi başlatılıyor...", "info")
    
    # 1. Action ID'den draftı bul
    res = manager.session.get(DRAFT_PAGE_URL)
    if "login.jsf" in res.url: login(); res = manager.session.get(DRAFT_PAGE_URL)
    
    df = html_tabloyu_parse_et(res.text)
    if df.empty: return None

    ilgili_satir = df[df["Created"] == target_date]
    if ilgili_satir.empty: return None
    
    copy_id = ilgili_satir.iloc[0]["Copy ID"]
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
            
            # --- YENİ DRAFT İSMİNİ BUL ---
            # Sayfadaki <input ... name="...:draft_name" value="YENİ_İSİM"> alanını çek
            soup_new = BeautifulSoup(new_page_res.text, 'html.parser')
            # ID genelde mainForm:draftInfo:0:draft_name veya benzeridir
            # Value'su dolu olan draft name inputunu bul
            name_input = soup_new.find("input", {"name": lambda x: x and "draft_name" in x})
            
            new_draft_name = "Bilinmeyen Kopya"
            if name_input:
                new_draft_name = name_input.get("value")
            
            manager.add_log(f"✅ Kopyalandı: {new_draft_name}")

            time.sleep(1) # Sistemin oturması için
            res_check = manager.session.get(DRAFT_PAGE_URL)
            df_check = html_tabloyu_parse_et(res_check.text)

            yeni_satir = df_check[df_check["Draft Name"] == new_draft_name]

            yeni_tarih = None
            if not yeni_satir.empty:
                yeni_tarih = yeni_satir.iloc[0]["Created"]
                new_from_loc = yeni_satir.iloc[0]["From"]
                
                target_keyword = " ".join(original_from_loc.lower().split())
                current_loc_cleaned = " ".join(new_from_loc.lower().split())
                
                adres_uyusuyor_mu = target_keyword in current_loc_cleaned
                
                if not adres_uyusuyor_mu:
                    manager.add_log(f"⚠️ Adres uyuşmazlığı! Beklenen: {original_from_loc} -> Gelen: {new_from_loc}", "warning")
                    
                    adresi_duzelt_backend(full_redirect_url, original_from_loc)
                else:
                    manager.add_log("✅ Adres doğrulandı.", "success")
            else:
                manager.add_log("⚠️ Yeni kopya listede henüz görünmüyor.", "warning")

            # --- DÖNÜŞ DEĞERİ: ARTIK SADECE İSİM DEĞİL, PAKET DÖNÜYORUZ ---
            if yeni_tarih:
                return {"name": new_draft_name, "date": yeni_tarih}
            else:
                return None
            
        except Exception as e: 
            print(f"Kopya isim hatası: {e}")
            return None
            
    return None

def drafti_planla_backend(target_date, draft_name):
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
        original_from_loc = target_row.iloc[0]["From"]

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
            firsat = analizi_yap(final_xml, draft_name)
            
            if firsat:
                # Kopyala ve yeni ismi döndür
                sonuc_paketi = drafti_kopyala(target_date, original_from_loc)
                if sonuc_paketi:
                    manager.add_log(f"{draft_name} için fırsat bulundu, kopyalanıyor...", "success")
                    
                    # --- KRİTİK: LİSTEYİ GÜNCELLE ---
                    # Otomatik görevde yeni kopyayı takip listesine ekle, eskisini çıkar
                    # Bu mantığı aşağıda `gorev` fonksiyonunda da yönetebiliriz ama buradan dönmek en temizi.
                    return sonuc_paketi 
            
            manager.add_log(f"{draft_name} tamamlandı, fırsat yok.", "warning")
            return None
            
        return None

    except Exception as e:
        manager.add_log(f"Hata ({draft_name}): {str(e)}", "error")
        return None

def adresi_duzelt_backend(draft_url, hedef_adres_keyword):
    manager.add_log(f"🛠️ Adres düzeltme operasyonu başlatılıyor... Hedef: {hedef_adres_keyword}", "warning")
    
    try:
        # --- ADIM 1: EDIT MODALINI AÇMAK ---
        
        # 1.a) Sayfadaki güncel verileri topla
        res_main = manager.session.get(draft_url)
        form_data = form_verilerini_topla(res_main.text)
        current_viewstate = form_data.get("javax.faces.ViewState")
        
        soup = BeautifulSoup(res_main.text, 'html.parser')

        edit_link = soup.find("a", title="Change 'Ship From' address")
        
        if not edit_link:
            edit_link = soup.find("a", id=re.compile(r"ship_from_address_edit"))
            
        if not edit_link:
            pencil_icon = soup.find("i", class_="pi-pencil")
            if pencil_icon:
                edit_link = pencil_icon.find_parent("a")

        if not edit_link:
            manager.add_log("❌ Adres düzenleme (Kalem) butonu sayfada bulunamadı!", "error")
            return False

        edit_btn_id = edit_link.get("id")
        
        # 1.b) Payload 1 Hazırla (Modalı Render Etmek İçin)
        payload_open = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": edit_btn_id,
            "javax.faces.partial.execute": edit_btn_id,
            "javax.faces.partial.render": "addressDialog:addressForm:addressTable", # Tabloyu render et
            edit_btn_id: edit_btn_id,
            "mainForm": "mainForm",
            **form_data # Sayfadaki diğer inputları da ekle
        }
        
        # 1.c) İsteği Gönder
        res_open = manager.session.post(draft_url, data=payload_open)
        
        # --- ADIM 2: MODAL İÇERİĞİNİ PARSE ET VE HEDEFİ BUL ---
        
        # 2.a) Yeni ViewState'i Yakala (Çok Önemli!)
        # Response 1 içinde ViewState güncelleniyor, onu almalıyız.
        match_vs = re.search(r'id=".*?javax\.faces\.ViewState.*?"><!\[CDATA\[(.*?)]]>', res_open.text)
        if match_vs:
            current_viewstate = match_vs.group(1)
            # Form data'yı güncelle
            form_data["javax.faces.ViewState"] = current_viewstate
            
        # 2.b) Modal HTML'ini Çıkar (CDATA içindedir)
        # addressDialog:addressForm:addressTable update bloğunu bul
        xml_soup = BeautifulSoup(res_open.text, 'xml') # XML parser kullanıyoruz response için
        update_tag = xml_soup.find("update", {"id": "addressDialog:addressForm:addressTable"}) # Tam ID'yi ara
        
        if not update_tag:
             # ID ile bulamazsa alternatif (Data Table ID'si genelde sabittir ama yine de)
             html_parts = re.findall(r'<!\[CDATA\[(.*?)]]>', res_open.text, re.DOTALL)
             modal_html = "".join(html_parts)
        else:
             modal_html = update_tag.text

        modal_soup = BeautifulSoup(modal_html, 'html.parser')
        
        # 2.c) Hedef Satırı ve RowKey'i Bul
        rows = modal_soup.find_all("tr", role="row")
        target_row_key = None
        target_row_index = None
        
        for index, row in enumerate(rows):
            # Satırdaki metinleri kontrol et
            row_text = row.get_text(" ", strip=True).lower()
            
            # Hedef kelime (örn: new jersey) satırda geçiyor mu?
            if hedef_adres_keyword.lower() in row_text:
                target_row_key = row.get("data-rk") # İŞTE BU! (ab2a6e...)
                target_row_index = row.get("data-ri")
                manager.add_log(f"✅ Hedef satır bulundu. Key: {target_row_key}", "info")
                break
        
        if not target_row_key:
            manager.add_log(f"❌ '{hedef_adres_keyword}' içeren satır modalda bulunamadı.", "error")
            return False

        # --- ADIM 3: SEÇİMİ GÖNDER (SELECT BUTTON CLICK) ---
        
        # 3.a) Select Butonunu Bul (Payload 2'deki source)
        # Genelde tablonun footer'ındadır veya ID'si j_idt ile başlar.
        # Senin paylaştığın payload'da kaynak: addressDialog:addressForm:addressTable:j_idt156
        # Modal HTML içinde butonu bulmaya çalışalım
        select_btn = modal_soup.find("button", text=lambda x: x and "Select" in x)
        if not select_btn:
             # Text ile bulamazsak class ile
             select_btn = modal_soup.find("button", class_="ui-button")
        
        # ID'yi dinamik alalım (Logda 156 bitiyordu ama değişebilir)
        select_btn_id = select_btn.get("id") if select_btn else "addressDialog:addressForm:addressTable:j_idt156"
        
        # Eğer modal HTML'inde ID tam path ile gelmiyorsa (bazen sadece son kısmı gelir),
        # Payload'daki örneği baz alarak prefix ekleyebiliriz.
        if ":" not in select_btn_id:
             select_btn_id = f"addressDialog:addressForm:addressTable:{select_btn_id}"

        # 3.b) Payload 2 Hazırla (Seçim İşlemi)
        # Modal içindeki form inputlarını (radio, inputs) toplayalım
        # JSF güvenlik gereği tablodaki inputları da isteyebilir.
        modal_inputs = form_verilerini_topla(modal_html)
        
        payload_select = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": select_btn_id,
            "javax.faces.partial.execute": "addressDialog:addressForm", # Tüm modal formunu execute et
            # "javax.faces.partial.render": "mainForm:draftInfo", # Senin payload'da render yoktu ama JSF genelde ister
            select_btn_id: select_btn_id,
            "addressDialog:addressForm": "addressDialog:addressForm", # Form ID
            "addressDialog:addressForm:addressTable_radio": "on", # Radio mode
            "addressDialog:addressForm:addressTable_selection": target_row_key, # KRİTİK VERİ BURADA
            "javax.faces.ViewState": current_viewstate,
            **modal_inputs # Modal içindeki input değerlerini ekle (satırlar vb.)
        }
        
        # 3.c) İsteği Gönder
        res_select = manager.session.post(draft_url, data=payload_select)
        
        if res_select.status_code == 200 and "error" not in res_select.text.lower():
            manager.add_log("✅ Adres başarıyla değiştirildi.", "success")
            return True
        else:
            manager.add_log("❌ Adres seçim isteği başarısız oldu.", "error")
            return False

    except Exception as e:
        manager.add_log(f"Adres düzeltme hatası: {e}", "error")
        return False

def gorev():
    # Artık st.session_state yerine Global Manager'dan listeyi alıyoruz
    current_list = manager.watch_list
    
    if not current_list:
        # manager.add_log("Takip listesi boş, kontrol atlandı.", "info")
        return

    manager.add_log(f"⏰ Periyodik kontrol başladı. ({len(current_list)} adet)", "info")
    
    yeni_liste = list(current_list) # Kopyasını al
    degisiklik_var = False
    
    for i, item in enumerate(current_list):
        d_name = item['name']
        d_date = item['date']
        
        sonuc_paketi = drafti_planla_backend(d_date, d_name)
        
        if sonuc_paketi:
            yeni_isim = sonuc_paketi['name']
            yeni_tarih = sonuc_paketi['date']
            
            manager.add_log(f"🔄 Listede güncelleniyor: {d_name} -> {yeni_isim}", "success")
            
            manager.watch_list[i] = {
                'name': yeni_isim,
                'date': yeni_tarih
            }
            print(f"✅ Takip listesi güncellendi: {yeni_isim} ({yeni_tarih})")

# --- SCHEDULER BAŞLATMA ---
@st.cache_resource
def start_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(gorev, 'interval', minutes=30, max_instances=1, misfire_grace_time=None)
    sched.start()
    return sched

scheduler = start_scheduler()

# --- UI TASARIMI ---
st.set_page_config(page_title="Kargo Paneli", layout="wide")

with st.sidebar:
    st.header("⚙️ Bot Ayarları")
    
    # Mil Ayarı
    yeni_sinir = st.number_input(
        "Fırsat Mil Sınırı (Mil)", 
        min_value=50, 
        max_value=3000, 
        value=manager.mile_threshold, # Varsayılan olarak manager'daki değeri göster
        step=50,
        help="Planlanan kargo bu mesafenin altındaysa otomatik kopya oluşturulur."
    )
    
    # Değer değişirse Manager'ı güncelle
    if yeni_sinir != manager.mile_threshold:
        manager.set_mile_threshold(yeni_sinir)
        st.toast(f"✅ Sınır güncellendi: {yeni_sinir} Mil")
        
    st.divider()
    st.caption(f"Aktif Sınır: **{manager.mile_threshold} Mil**")

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
        current_acc = st.session_state.selected_account
        label = f"{current_acc['flag']} {current_acc['name']}"
        
        # Popover (Açılır Menü) - use_container_width=True kutuyu sütuna yayar
        with st.popover(label, use_container_width=True):
            st.caption("Hesap Değiştir")
            for acc in ACCOUNTS:
                # Her satırı İsim ve İkon olarak ikiye böl
                
                is_selected = (acc['id'] == current_acc['id'])
                btn_style = "primary" if is_selected else "secondary"
                
                if st.button(f"{acc['flag']} {acc['name']}", 
                             key=f"sel_{acc['id']}", 
                             type=btn_style, 
                             use_container_width=True): # Tam genişlik
                    change_account(acc)
                    st.rerun()
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
                    current.append({'name': row['Draft Name'], 'date': new_date})
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
            "date": "Created"
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

