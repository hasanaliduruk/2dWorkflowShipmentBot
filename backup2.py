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

if 'WATCH_LIST' not in st.session_state:
    st.session_state.WATCH_LIST = []

# --- KONFIGURASYON ---
try:
    TEAMS_WEBHOOK_URL = st.secrets["TEAMS_WEBHOOK"]
    USER_EMAIL = st.secrets["DB_EMAIL"]
    USER_PASS = st.secrets["DB_PASS"]
except:
    TEAMS_WEBHOOK_URL = "SENIN_WEBHOOK_URL"
    USER_EMAIL = ""
    USER_PASS = ""

BASE_URL = "https://app.2dworkflow.com"
LOGIN_URL = f"{BASE_URL}/login.jsf"
DRAFT_PAGE_URL = f"{BASE_URL}/draft.jsf"
PLAN_URL = f"{BASE_URL}/draftplan.jsf"

if 'session' not in st.session_state:
    st.session_state.session = requests.Session()
    st.session_state.session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })

# Yeni oluşturulan kopyaların seçili gelmesi için state yönetimi
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

def analizi_yap(xml_response, ui_logger=None):
    if ui_logger: ui_logger.write("📊 Sonuçlar analiz ediliyor...")
    
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
                    
                    if mil < 500:
                        detay = f"✅ **FIRSAT! {mil} Mil**\nPlan: {current_option}\nDepo: {dest}"
                        msg += f"{detay}\n\n"
                        if ui_logger: ui_logger.success(f"Fırsat: {mil} Mil ({dest})")
                        firsat_bulundu = True
                    else:
                        if ui_logger: ui_logger.write(f"❌ {mil} Mil ({dest}) - Uygun değil")
                except: pass
    
    if msg: teams_bildirim_gonder(msg)
    return firsat_bulundu

def poll_results_until_complete(session, base_payload, referer_url, ui_progress_bar=None, ui_status_text=None):
    max_retries = 60
    if ui_status_text: ui_status_text.update(label="Amazon planlıyor...", state="running")
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
                if ui_progress_bar: ui_progress_bar.progress(100)
                return res.text
            
            match_percent = re.search(r'>\s*(\d+)\s*%\s*<', res.text)
            current_percent = int(match_percent.group(1)) if match_percent else 0
            
            if ui_progress_bar and current_percent > 0: ui_progress_bar.progress(current_percent)
            if ui_status_text: ui_status_text.update(label=f"İlerleme: %{current_percent}", state="running")

            if current_percent == 0 and last_percent > 50: return res.text
            if current_percent > last_percent: last_percent = current_percent

            time.sleep(5)
        except: time.sleep(5)
    return None

def drafti_kopyala(original_draft_action_id, ui_logger=None):
    """
    Kopyalama yapar ve YENİ OLUŞAN DRAFT'IN ADINI döndürür.
    """
    if ui_logger: ui_logger.write("📋 Kopyalama başlatılıyor...")
    
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
            
            if ui_logger: ui_logger.success(f"✅ Kopyalandı: {new_draft_name}")
            return new_draft_name
            
        except Exception as e: 
            print(f"Kopya isim hatası: {e}")
            return None
            
    return None

def drafti_planla_backend(action_id_open_button, draft_name, ui_container):
    s = st.session_state.session

    status = None
    p_bar = None

    if ui_container:
        # Eğer UI varsa (Butona basıldıysa) ekrana çiz
        with ui_container:
            status = st.status(f"İşleniyor: {draft_name}", expanded=True)
            p_bar = status.progress(0)
    else:
        # UI yoksa (Otomatik arka plan göreviyse) sadece terminale yaz
        print(f"🔄 Otomatik Görev Başladı: {draft_name}")
        
    try:
        # 1. Draft Aç
        if status: status.write("📂 Draft açılıyor...")
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
            status.error("Draft açılamadı.")
            return None # Return None = Kopyalama olmadı

        s.get(redirect_url) # Detay sayfası
        
        # 2. Planlama
        status.write("🚀 Planlama başlatılıyor...")
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
             status.error("Planlama hatası.")
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
            ui_progress_bar=p_bar, 
            ui_status_text=status
        )
        
        if final_xml:
            firsat = analizi_yap(final_xml, ui_logger=status)
            
            if firsat:
                # Kopyala ve yeni ismi döndür
                yeni_isim = drafti_kopyala(action_id_open_button, ui_logger=status)
                if yeni_isim:
                    if status: status.update(label=f"✅ {draft_name} -> {yeni_isim} (Kopyalandı)", state="complete")
                    
                    # --- KRİTİK: LİSTEYİ GÜNCELLE ---
                    # Otomatik görevde yeni kopyayı takip listesine ekle, eskisini çıkar
                    # Bu mantığı aşağıda `gorev` fonksiyonunda da yönetebiliriz ama buradan dönmek en temizi.
                    return yeni_isim 
            
            if status: status.update(label=f"✅ {draft_name} Tamamlandı (Fırsat Yok)", state="complete", expanded=False)
            return None # Fırsat yoksa None dön
            
        return None

    except Exception as e:
        status.error(f"Hata: {e}")
        return None

def gorev():
    print(f"⏰ [{datetime.now().strftime('%H:%M')}] Periyodik kontrol başladı...")
    
    # Session state'e erişim scheduler thread'inde zor olabilir.
    # Ancak Streamlit'in yeni sürümlerinde bu genelde çalışır.
    # Eğer hata alırsan global bir değişken kullanmak gerekebilir.
    
    takip_listesi = st.session_state.get('WATCH_LIST', [])
    
    if not takip_listesi:
        print("📭 Takip listesi boş. İşlem yapılmadı.")
        return

    # Listeyi kopyala (Döngü sırasında liste değişirse hata almamak için)
    # Ayrıca index ile döneceğiz ki güncelleme yapabilelim
    for index, item in enumerate(takip_listesi):
        d_name = item['name']
        a_id = item['id']
        
        print(f"   🔎 Kontrol ediliyor: {d_name}")
        
        # UI Container GÖNDERMİYORUZ (None), böylece sessiz çalışıyor
        # Fonksiyon yeni kopya ismini döndürürse güncelleme yapacağız
        yeni_kopya_ismi = drafti_planla_backend(a_id, d_name, ui_container=None)
        
        if yeni_kopya_ismi:
            print(f"   🔄 KOPYA OLUŞTU! Listede güncelleniyor: {yeni_kopya_ismi}")
            
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

# --- UI KATMANI ---
st.set_page_config(page_title="Kargo Paneli", layout="wide")
st.title("📑 Otomatik Kargo Planlayıcı")

with st.sidebar:
    if st.button("🔄 Listeyi Yenile"):
        # Listeyi manuel yenilerken seçimleri sıfırla
        st.session_state.auto_select_drafts = []
        st.cache_data.clear()
        st.rerun()

# Dataframe'i getir (Session'daki auto_select_drafts'a göre seçimleri yapacak)
df, hata = veriyi_dataframe_yap()

if hata:
    st.error(hata)
else:
    # Tabloyu göster
    edited_df = st.data_editor(
        df,
        column_config={
            "Seç": st.column_config.CheckboxColumn("İşle", default=False),
            "Action ID": None,
            "Copy ID": None
        },
        disabled=["Draft Name", "From", "Created"],
        hide_index=True,
        use_container_width=True,
        key="draft_editor"
    )

    secili_satirlar = edited_df[edited_df["Seç"] == True]

    if st.button(f"🚀 Seçili {len(secili_satirlar)} Taslağı Başlat ve Takibe Al"):
        if secili_satirlar.empty:
            st.warning("Lütfen seçim yapın.")
        else:
            # 1. Takip Listesini Sıfırla ve Doldur
            st.session_state.WATCH_LIST = []
            for index, row in secili_satirlar.iterrows():
                st.session_state.WATCH_LIST.append({
                    'name': row['Draft Name'], 
                    'id': row['Action ID']
                })
            
            st.success(f"✅ {len(secili_satirlar)} taslak otomatik takip listesine eklendi (30dk arayla).")
            
            # 2. Anlık İşlemi Başlat (Görsel Olarak)
            ui_containers = {}
            st.write("--- Anlık İşlem Kuyruğu ---")
            for index, row in secili_satirlar.iterrows():
                ui_containers[row['Action ID']] = st.container()
            
            yeni_kopyalar = []
            
            for index, row in secili_satirlar.iterrows():
                draft_adi = row['Draft Name']
                action_id = row['Action ID']
                
                # UI ile çalıştır
                yeni_isim = drafti_planla_backend(
                    action_id, 
                    draft_adi, 
                    ui_containers[action_id]
                )
                
                if yeni_isim:
                    yeni_kopyalar.append(yeni_isim)
                    # Takip listesindeki eski ismin yerine yenisini koymak mantıklı olabilir
                    # Ama sayfa yenilenince ID'ler değişeceği için en temiz yöntem:
                    # Sayfa yenilensin, kullanıcı yeni kopyaları tekrar seçip başlatsın.
            
            if yeni_kopyalar:
                st.session_state.auto_select_drafts = yeni_kopyalar
                st.success("Kopyalar oluşturuldu, liste güncelleniyor...")
                time.sleep(2)
                st.rerun()

@st.cache_resource
def start_scheduler():
    sched = BackgroundScheduler()
    # Test için 30 saniye yaptım, çalışınca minutes=30 yaparsın
    sched.add_job(gorev, 'interval', seconds=30) 
    sched.start()
    return sched

scheduler_status = start_scheduler()