import streamlit as st
import pandas as pd
from datetime import datetime
import io
import time
from bot.constants import (
    BASE_URL,
    LOGIN_URL,
    DRAFT_PAGE_URL,
    PLAN_URL,
    USER_AGENT,
)
from bot.manager import GlobalManager
from bot.jsf import form_verilerini_topla
from bot.scheduler import safe_run
from bot.auth import login, fetch_accounts_backend, switch_account_backend
from bot.drafts import veriyi_dataframe_yap




@st.cache_resource
def get_manager():
    return GlobalManager()
@st.cache_resource
def get_global_bot_store():
    """
    Returns a dictionary that persists across browser sessions.
    Format: {'user_email': GlobalManager_Instance}
    """
    return {}
# manager = get_manager()

# ----- CONFIG -----
try:
    TEAMS_WEBHOOK_URL = st.secrets["TEAMS_WEBHOOK"]
except:
    TEAMS_WEBHOOK_URL = ""

# --- FONKSİYONLAR ---





# --- MAIN APPLICATION FLOW ---

def main():
    st.set_page_config(page_title="2DWorkflow Bot", layout="wide")
    st.markdown("""
        <style>
               /* Reduce top padding */
               .block-container {
                    padding-top: 1rem;
                    padding-bottom: 1rem;
                    padding-left: 2rem;
                    padding-right: 2rem;
                }
                /* Compact Data Editor/Dataframe cells */
                div[data-testid="stDataEditor"] div[data-testid="stDataFrame"] table {
                    font-size: 0.85rem !important;
                }
                /* Reduce vertical gap between elements */
                div[data-testid="stVerticalBlock"] > div {
                    gap: 0.5rem;
                }
        </style>
        """, unsafe_allow_html=True)
    BOT_STORE = get_global_bot_store()
    
    # 1. Check Session State
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    # 2. SHOW LOGIN SCREEN (If not authenticated)
    if not st.session_state.authenticated:
        col1, col2, col3 = st.columns([1, 1.5, 1])
        with col2:
            st.title("🔒 2DWorkflow Giriş")
            st.caption("Verileriniz kaydedilmez. Doğrudan 2DWorkflow üzerinden giriş yapılır.")
            
            email_input = st.text_input("E-Posta Adresi")
            pass_input = st.text_input("Şifre", type="password")
            
            if st.button("Giriş Yap", width="stretch", type="primary"):
                if not email_input or not pass_input:
                    st.error("Lütfen tüm alanları doldurun.")
                else:
                    with st.spinner("Bağlanılıyor..."):
                        # CHECK 1: Is there already a running bot for this user?
                        if email_input in BOT_STORE:
                            # YES! Re-attach to the existing bot
                            existing_mgr = BOT_STORE[email_input]
                            
                            # Update credentials in case they changed (optional)
                            existing_mgr.password = pass_input 
                            
                            st.session_state.authenticated = True
                            st.session_state.my_manager = existing_mgr
                            st.success("Aktif oturum bulundu, bağlanıldı!")
                            time.sleep(1)
                            st.rerun()
                        
                        # NO: This is a fresh login. Verify credentials first.
                        else:
                            temp_mgr = GlobalManager(email_input, pass_input, TEAMS_WEBHOOK_URL)
                            success = login(temp_mgr)
                            
                            if success:
                                # Save to Global Store so it survives logout
                                BOT_STORE[email_input] = temp_mgr
                                
                                st.session_state.authenticated = True
                                st.session_state.my_manager = temp_mgr
                                st.rerun()
                            #else:
                                #st.error(msg)
                                # Don't delete temp_mgr explicitly, just let it go out of scope
        return

    # 3. SHOW DASHBOARD (If authenticated)
    
    # Retrieve the user's personal manager
    manager = st.session_state.my_manager
    
    # Sidebar Logout
    with st.sidebar:
        st.write(f"👤 **{manager.email}**")
        if st.button("Çıkış Yap"):
            
            st.session_state.authenticated = False
            if "my_manager" in st.session_state:
                del st.session_state.my_manager
            st.rerun()
        st.divider()
        # ... your sidebar settings ...

    # --- SIDEBAR SETTINGS ---
    with st.sidebar:
        st.header("⚙️ Ayarlar")
        
        # --- SCHEDULER SETTINGS ---
        mode_label = st.radio(
            "Zamanlama Modu", 
            ["Dakika Bazlı (Interval)", "Saat Başı ve Buçuk (00, 30)", "Çeyrek Saatler (00, 15, 30, 45)"],
            captions=["Belirlediğiniz dakika aralığında çalışır.", "Her saat başı ve buçukta (örn 14:00, 14:30) çalışır.", "Her 15 dakikada bir (örn 14:15, 14:45) çalışır."]
        )
        
        # Map label to internal value
        new_mode = "interval"
        if "Saat Başı" in mode_label: new_mode = "half_hourly"
        elif "Çeyrek" in mode_label: new_mode = "quarterly"
        
        if new_mode != manager.scheduler_mode:
            manager.scheduler_mode = new_mode
            if manager.is_running: manager.start_bot_process() # Restart with new mode
            st.toast("✅ Zamanlayıcı güncellendi")

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
        if manager.scheduler_mode == "interval":
            min_limit = st.number_input("Tekrar deneme dakikası", min_value=1, max_value=500, value=manager.mins_threshold, step=5)
            if min_limit != manager.mins_threshold:
                manager.mins_threshold = min_limit
                if manager.is_running: manager.start_bot_process()
                st.toast("✅ Zamanlayıcı güncellendi")
            
        st.divider()
        st.caption(f"Aktif Mil Sınır: **{manager.mile_threshold} Mil**")
        if manager.scheduler_mode == "interval":
            st.caption(f"Aktif Dakika Sınır: **{manager.mins_threshold} Dakika**")

    #st.title("📑 Otomatik Kargo Botu")
    title_text = "2D Workflow Bot"
    st.markdown(f"""
        <style>
        /* 1. Ensure the tab list takes full width so we can push content to the right */
        div[data-baseweb="tab-list"] {{
            display: flex;
            margin-top: 20px;
            width: 100%;
        }}
        
        /* 2. Create a pseudo-element after the last tab */
        div[data-baseweb="tab-list"]::after {{
            content: "{title_text}"; /* The text to display */
            margin-left: auto;            /* Pushes this element to the far right */
            align_self: center;           /* Vertically center it */
            margin-right: 1rem;           /* Add some right padding */
            
            font-weight: bold;
            font-size: 1.5rem;
            padding: 0;
            color: white;        /* Dynamic color */
        }}
        </style>
    """, unsafe_allow_html=True)
    tab_selection, tab_dashboard, tab_logs = st.tabs([ "Taslak Seçimi", "Aktif Takip (Dashboard)", "Loglar"])

    with tab_dashboard:
        if manager.history:
            st.success(f"🎉 Toplam {len(manager.history)} işlemde fırsat yakalandı!")
            
            # Convert deque to DataFrame
            history_df = pd.DataFrame(manager.history)
            
            st.dataframe(
                history_df,
                column_config={
                    "account": st.column_config.TextColumn("Hesap", width="medium"),
                    "name": st.column_config.TextColumn("📦 İşlenen Taslak", width="medium"),
                    "found": st.column_config.TextColumn("🎯 Bulunanlar", width="large"),
                    "time": st.column_config.TextColumn("🕒 Zaman", width="small")
                },
                hide_index=True,
                width="stretch"
            )
            
            if st.button("Geçmişi Temizle"):
                manager.history.clear()
                st.rerun()

    with tab_selection:
        

        header_col, title_col, menu_col = st.columns([1, 5, 1], gap="small")

        with header_col:
            if st.button("🔄 Taslakları Yenile"):
                st.cache_data.clear()
                st.rerun()
        with title_col:
            st.subheader("Taslaklar", text_alignment="center")
        with menu_col:
            # Seçili olanı göster
            current_name = manager.current_account_name
            label = f"🏢 {current_name}"
            
            # Popover (Açılır Menü)
            with st.popover(label, width="stretch"):
                st.caption("Hesap Değiştir")
                
                # DURUM 1: Henüz hesaplar çekilmediyse "Getir" butonu göster
                if not manager.available_accounts:
                    with st.spinner("Hesaplar çekiliyor..."):
                            if not manager.session.cookies: 
                                login(manager)
                            
                            fetch_success = fetch_accounts_backend(manager)
                            
                            if fetch_success:
                                st.success("Listelendi!")
                                time.sleep(0.5)
                                st.rerun()
                            else:
                                st.error("Çekilemedi.")
                    # FIX: Logic is now INSIDE the button check
                    if st.button("Hesapları Getir", key="fetch_acc_btn", width="stretch"):
                        with st.spinner("Hesaplar çekiliyor..."):
                            if not manager.session.cookies: 
                                login(manager)
                            
                            fetch_success = fetch_accounts_backend(manager)
                            
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
                                    width="stretch"):
                            
                            with st.spinner(f"{acc['name']} hesabına geçiliyor..."):
                                success = switch_account_backend(manager, acc['id'])
                                if success:
                                    st.success("Geçiş yapıldı!")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("Geçiş başarısız.")
        df, hata = veriyi_dataframe_yap(manager)
        
        if df is not None and not df.empty:
            desired_order = [
                "Seç", 
                "Max Mil",
                "Hedef Depolar",
                "Draft Name", 
                "From", 
                "Created", 
                "SKUs", 
                "Units"
            ]
            grid_response = st.data_editor(
                df,
                column_order=desired_order,
                column_config={
                    "Seç": st.column_config.CheckboxColumn("Ekle", default=False),
                    "Max Mil": st.column_config.NumberColumn("Max Mil", step=50, help="Bu taslak için özel mil sınırı"),
                    "Hedef Depolar": st.column_config.TextColumn("Hedef Depolar", help="Örn: AVP1, TEB3 (Virgülle ayırın)"),
                    "Draft Name": st.column_config.TextColumn("Taslak Adı", width="large"),
                    "From": st.column_config.TextColumn("From", width="medium"),
                    "Created": st.column_config.TextColumn("Oluşturulma Tarihi", width="medium"),
                    "SKUs": st.column_config.TextColumn("SKUs", width="small"),
                    "Units": st.column_config.NumberColumn("Units", width="small"),
                    "Action ID": None,
                    "Copy ID": None,
                    "Name Input ID": None
                },
                disabled=["Draft Name", "From", "Created", "SKUs", "Units"],
                hide_index=True,
                width='stretch',
                key="draft_selector"
            )
            
            secili_satirlar = grid_response[grid_response["Seç"] == True]
            
            if st.button(f"➕ Seçili {len(secili_satirlar)} Taslağı Takibe Ekle"):
                # GUARD: Ensure we know the current account
                if not manager.current_account_id:
                    st.error("⚠️ Aktif hesap ID'si bulunamadı. Lütfen önce 'Hesapları Getir' butonuna basın.")
                else:
                    added_count = 0
                    for index, row in secili_satirlar.iterrows():
                        key_date = row['Created']
                        
                        # Check existence (O(1) speed!)
                        if key_date not in manager.watch_list:
                            manager.watch_list[key_date] = {
                                'account_id': manager.current_account_id,
                                'account_name': manager.current_account_name,
                                'name': row['Draft Name'], 
                                'date': key_date, 
                                'loc': row["From"],
                                'max_mile': int(row["Max Mil"]),
                                'targets': str(row["Hedef Depolar"]),
                                'found_warehouses': [],
                            }
                            added_count += 1
                    
                    if added_count > 0:
                        st.success(f"{added_count} eklendi.")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.warning("Seçilenler zaten listede.")

    with tab_logs:
        if st.button("Logları Yenile"):
            pass # Sadece rerun tetikler
        
        log_container = st.container(height=400)
        with log_container:
            for log in manager.logs:
                st.text(log)
                
        # Otomatik yenileme notu
        st.caption("Loglar arka planda birikir. Sayfayı yenileyerek veya butona basarak görebilirsiniz.")

    
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
            if st.button("BAŞLAT", help="Botu Başlat", type="secondary", width="stretch", disabled=manager.is_running, ):
                manager.is_running = True
                manager.add_log("▶️ Bot başlatıldı.", "success")
                manager.start_bot_process()
                if manager.scheduler_mode == "interval":
                    try:
                        # Trigger immediate run
                        manager.scheduler.add_job(safe_run, 'date', run_date=datetime.now(), args=[manager])
                        st.toast("Bot başlatıldı, ilk kontrol yapılıyor...")
                    except: pass
                st.rerun()

        with stop_btn_col:
            # Stop Button
            if st.button("DURDUR", help="Botu Durdur", type="secondary", width="stretch", disabled=not manager.is_running):
                manager.is_running = False
                manager.stop_bot_process()
                manager.add_log("⏹️ Bot durduruldu.", "warning")
                st.toast("Bot durduruldu.")
                st.rerun()
    if manager.is_running:
        job = manager.scheduler.get_job('user_task')
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime("%H:%M:%S")
            st.info(f"⏳ **Sonraki Planlanmış Çalışma:** {next_run}")
        else:
            st.warning("⚠️ Bot çalışıyor ama zamanlayıcı bulunamadı.")

    # --- DATAFRAME EDITOR ---
    watch_df = manager.get_watch_list_df()

    if not watch_df.empty:
        visible_cols = ["account_name", "name", "max_mile", "targets", "loc", "date", "found_warehouses"]
        display_df = watch_df[[c for c in visible_cols if c in watch_df.columns]]
        edited_watch_df = st.data_editor(
            display_df,
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
        new_data = edited_watch_df.to_dict("records")
        if str(new_data) != str(st.session_state.get('last_saved_data', '')):
            manager.update_watch_list_from_df(new_data)
            st.session_state['last_saved_data'] = str(new_data) # Cache for next comparison
            st.toast("✅ Değişiklikler otomatik kaydedildi!", icon="💾")
    else:
        st.info("Takip listesi şu an boş. Yukarıdan taslak seçip ekleyin.")

if __name__ == "__main__":
    main()
