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
        # Takip listesi (Dictionary listesi)
        self.watch_list = []
        # Loglar (Son 50 logu tutar)
        self.logs = deque(maxlen=50)
        
    def add_log(self, message, type="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = "info"
        if type == "success": icon = "✅"
        elif type == "error": icon = "❌"
        elif type == "warning": icon = "⚠️"
        else: icon = "ℹ️"
        
        log_entry = f"{timestamp} {icon} {message}"
        self.logs.appendleft(log_entry) # En yeniyi başa ekle
        print(log_entry) # Terminale de yazsın

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
    # Buraya kendi bilgilerini gir
    TEAMS_WEBHOOK_URL = ""
    USER_EMAIL = ""
    USER_PASS = ""

BASE_URL = "https://app.2dworkflow.com"
LOGIN_URL = f"{BASE_URL}/login.jsf"
DRAFT_PAGE_URL = f"{BASE_URL}/draft.jsf"
PLAN_URL = f"{BASE_URL}/draftplan.jsf"

if 'session' not in st.session_state:
    st.session_state.session = requests.Session()
    # Header ayarları vs...
    st.session_state.session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    })

# Yeni oluşturulan kopyaların seçili gelmesi için state
if 'auto_select_drafts' not in st.session_state:
    st.session_state.auto_select_drafts = []

s = st.session_state.session

# --- FONKSİYONLAR ---

def login():
    """Siteye giriş yapar."""

    try:
        # Önce login sayfasına gidip ViewState alalım
        res = s.get(LOGIN_URL)
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

        post_res = s.post(LOGIN_URL, data=payload, headers={"Referer": LOGIN_URL})

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
    
    veri_listesi = []
    for row in rows:
        cells = row.find_all("td")
        if not cells or len(cells) < 9: continue
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
            created_date = cells[8].get_text(strip=True)
            
            # --- AUTO SELECT MANTIĞI ---
            # Eğer bu draft ismi, oluşturduğumuz kopyalar listesindeyse TRUE yap
            secili_mi = False
            if draft_name in st.session_state.auto_select_drafts:
                secili_mi = True
            
            veri_listesi.append({
                "Action ID": row_action_id,
                "Copy ID": copy_action_id,
                "Seç": secili_mi, # Dinamik seçim
                "Draft Name": draft_name,
                "From": from_loc,
                "Created": created_date
            })
        except: continue
    return pd.DataFrame(veri_listesi)

def veriyi_dataframe_yap():
    if not s.cookies:
        if not login(): return None, "Giriş Yapılamadı"
    try:
        response = s.get(DRAFT_PAGE_URL)
        if "login.jsf" in response.url: login(); response = s.get(DRAFT_PAGE_URL, headers={"Referer": DRAFT_PAGE_URL})
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
    try: requests.post(TEAMS_WEBHOOK_URL, json=payload, headers={'Content-Type': 'application/json'})
    except: pass

def analizi_yap(xml_response):
    manager.add_log("📊 Sonuçlar analiz ediliyor...")
    
    html_parts = re.findall(r'<!\[CDATA\[(.*?)]]>', xml_response, re.DOTALL)
    full_html = "".join(html_parts)
    soup = BeautifulSoup(full_html, 'html.parser')
    
    plans_table = soup.find("tbody", id=lambda x: x and "plans" in x)
    if not plans_table: return False

    rows = plans_table.find_all("tr")
    current_option = "Bilinmiyor"
    firsat_bulundu = False
    msg = ""
    
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
                    
                    if mil < 1500:
                        detay = f"✅ FIRSAT! {mil} Mil - Plan: {current_option} - Depo: {dest}"
                        manager.add_log(detay, "success")
                        teams_bildirim_gonder(detay)
                        firsat_bulundu = True
                    else:
                        manager.add_log(f"❌ {mil} Mil ({dest}) - Uygun değil")
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

            if "mainForm:plans" in res.text or "Amazon Optimized Splits" in res.text:
                return res.text
            
            match_percent = re.search(r'>\s*(\d+)\s*%\s*<', res.text)
            current_percent = int(match_percent.group(1)) if match_percent else 0

            if current_percent == 0 and last_percent > 50: return res.text
            if current_percent > last_percent: last_percent = current_percent
            time.sleep(5)
        except: time.sleep(5)
    return None

def drafti_kopyala(original_draft_action_id):
    """
    Kopyalama yapar ve YENİ OLUŞAN DRAFT'IN ADINI döndürür.
    """
    manager.add_log("Kopyalama işlemi başlatılıyor...", "info")
    
    # 1. Action ID'den draftı bul
    res = s.get(DRAFT_PAGE_URL)
    if "login.jsf" in res.url: login(); res = s.get(DRAFT_PAGE_URL)
    
    df = html_tabloyu_parse_et(res.text)
    if df.empty: return None

    ilgili_satir = df[df["Action ID"] == original_draft_action_id]
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
    res_confirm = s.post(DRAFT_PAGE_URL, data={**form_data, **copy_payload})
    
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
    
    res_final = s.post(DRAFT_PAGE_URL, data=confirm_payload)

    # 4. Redirect ve Yeni İsim Alma
    if "<redirect" in res_final.text:
        try:
            redirect_part = res_final.text.split('url="')[1].split('"')[0].replace("&amp;", "&")
            full_redirect_url = urllib.parse.urljoin(BASE_URL, redirect_part)
            
            # Yeni sayfaya git
            new_page_res = s.get(full_redirect_url)
            
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
            return new_draft_name
            
        except Exception as e: 
            print(f"Kopya isim hatası: {e}")
            return None
            
    return None

def drafti_planla_backend(action_id_open_button, draft_name):
    try:
        # 1. Draft Aç
        manager.add_log(f"İşlem başladı: {draft_name}", "info")
        main_res = s.get(DRAFT_PAGE_URL)
        if "login.jsf" in main_res.url: login(); main_res = s.get(DRAFT_PAGE_URL)

        form_data = form_verilerini_topla(main_res.text)
        action_payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": action_id_open_button,
            "javax.faces.partial.execute": "@all",
            action_id_open_button: action_id_open_button, 
            "mainForm": "mainForm"
        }
        res_open = s.post(DRAFT_PAGE_URL, data={**form_data, **action_payload})
        
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

        s.get(redirect_url) # Detay sayfası
        
        # 2. Planlama
        manager.add_log("🚀 Planlama başlatılıyor...")
        detay_res = s.get(redirect_url)
        detay_form_data = form_verilerini_topla(detay_res.text)
        create_plan_params = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "mainForm:create_plan",
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "mainForm",
            "mainForm:create_plan": "mainForm:create_plan",
            "mainForm": "mainForm"
        }
        res_plan = s.post(PLAN_URL, data={**detay_form_data, **create_plan_params}, headers={"Referer": redirect_url})
        
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
            s, 
            detay_form_data, 
            redirect_url, 
        )
        
        if final_xml:
            firsat = analizi_yap(final_xml)
            
            if firsat:
                # Kopyala ve yeni ismi döndür
                yeni_isim = drafti_kopyala(action_id_open_button)
                if yeni_isim:
                    manager.add_log(f"{draft_name} için fırsat bulundu, kopyalanıyor...", "success")
                    
                    # --- KRİTİK: LİSTEYİ GÜNCELLE ---
                    # Otomatik görevde yeni kopyayı takip listesine ekle, eskisini çıkar
                    # Bu mantığı aşağıda `gorev` fonksiyonunda da yönetebiliriz ama buradan dönmek en temizi.
                    return yeni_isim 
            
            manager.add_log(f"{draft_name} tamamlandı, fırsat yok.", "warning")
            return None
            
        return None

    except Exception as e:
        manager.add_log(f"Hata ({draft_name}): {str(e)}", "error")
        return None

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
        a_id = item['id']
        
        yeni_kopya_ismi = drafti_planla_backend(a_id, d_name)
        
        if yeni_kopya_ismi:
            manager.add_log(f"🔄 Listede güncelleniyor: {d_name} -> {yeni_kopya_ismi}", "success")
            
            # --- LİSTE GÜNCELLEME ---
            # Yeni kopyanın Action ID'sini bulmamız lazım.
            # Bunun için sayfayı bir kez çekip parse etmeliyiz.
            try:
                res = s.get(DRAFT_PAGE_URL)
                df = html_tabloyu_parse_et(res.text)
                
                # Yeni ismi listede bul
                yeni_satir = df[df["Draft Name"] == yeni_kopya_ismi]
                
                if not yeni_satir.empty:
                    yeni_action_id = yeni_satir.iloc[0]["Action ID"]
                    
                    # Watch List'teki bu öğeyi güncelle
                    st.session_state.WATCH_LIST[index] = {
                        'name': yeni_kopya_ismi,
                        'id': yeni_action_id
                    }
                    print(f"   ✅ Takip listesi güncellendi: {d_name} -> {yeni_kopya_ismi}")
                else:
                    print("   ⚠️ Yeni kopya listede bulunamadı (Zamanlama sorunu olabilir).")
            except Exception as e:
                print(f"   ❌ Liste güncelleme hatası: {e}")

# --- UI TASARIMI ---
st.set_page_config(page_title="Kargo Paneli", layout="wide")
st.title("📑 Otomatik Kargo Botu")

# 1. BÖLÜM: TAKİP LİSTESİ YÖNETİMİ
st.subheader("📋 Aktif Takip Listesi")
watch_df = manager.get_watch_list_df()

if not watch_df.empty:
    # Kullanıcıya silme imkanı veren editör
    edited_watch_df = st.data_editor(
        watch_df,
        column_config={
            "name": "Taslak Adı",
            "id": "Action ID"
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

st.divider()

# 2. BÖLÜM: TASLAK SEÇİMİ (MEVCUT LİSTE)
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📦 Mevcut Taslaklar")
    if st.button("🔄 Taslakları Yenile"):
        st.cache_data.clear()
        st.rerun()

    df, hata = veriyi_dataframe_yap()
    
    if df is not None and not df.empty:
        grid_response = st.data_editor(
            df,
            column_config={
                "Seç": st.column_config.CheckboxColumn("Ekle", default=False),
            },
            disabled=["Draft Name", "From", "Created"],
            hide_index=True,
            width='stretch',
            key="draft_selector"
        )
        
        secili_satirlar = grid_response[grid_response["Seç"] == True]
        
        if st.button(f"➕ Seçili {len(secili_satirlar)} Taslağı Takibe Ekle"):
            current = manager.watch_list
            for index, row in secili_satirlar.iterrows():
                # Zaten listede yoksa ekle
                if not any(d['id'] == row['Action ID'] for d in current):
                    current.append({'name': row['Draft Name'], 'id': row['Action ID']})
            
            manager.update_watch_list(current)
            st.success("Seçilenler takip listesine eklendi!")
            st.rerun()

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

# --- SCHEDULER BAŞLATMA ---
@st.cache_resource
def start_scheduler():
    sched = BackgroundScheduler()
    sched.add_job(gorev, 'interval', seconds=30) 
    sched.start()
    return sched

scheduler = start_scheduler()