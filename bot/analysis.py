from bs4 import BeautifulSoup
import re

from bot.notify import teams_bildirim_gonder

def analizi_yap(mgr, xml_response, draft_item):

    """
    Returns:
    - True: Opportunity found (Copy)
    - False: Continue waiting
    - "STOP": Bad keyword found (Remove from list)
    """

    mgr.add_log("📊 Sonuçlar analiz ediliyor...")
    
    draft_name = draft_item.get('name', 'Bilinmiyor')
    limit_mile = draft_item.get('max_mile', mgr.mile_threshold)
    target_warehouses_str = draft_item.get('targets', "")
    known_warehouses = draft_item.get('found_warehouses', [])

    html_parts = re.findall(r'<!\[CDATA\[(.*?)]]>', xml_response, re.DOTALL)
    full_html = "".join(html_parts)
    soup = BeautifulSoup(full_html, 'html.parser')
    
    plans_table = soup.find("tbody", id=lambda x: x and "plans" in x)
    if not plans_table: return False

    rows = plans_table.find_all("tr")
    current_option = "Bilinmiyor"

    target_list = [t.strip().upper() for t in target_warehouses_str.split(',') if t.strip()]
    previously_found = set(k.upper() for k in known_warehouses)
    
    bulunan_firsatlar = {} # Dictionary to store merged results
    firsat_sayisi = 0
    found_new = {"found_new": []}

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
                    dest = dest.split(":")[0]
                    
                    if "Amazon Optimized" in current_option: continue
                    
                    # --- PRIORITY 1: TARGET WAREHOUSE (STOP CONDITION) ---
                    if any(target in dest for target in target_list):
                        mgr.add_log(f"🎯 HEDEF DEPO BULUNDU! ({dest}) - Takip Bitiyor.", "success")
                        teams_bildirim_gonder(
                            mgr=mgr,
                            title="🎯 Hedef Depo Yakalandı!",
                            message=f"**{draft_name}** için hedef depo (**{dest}**) bulundu. Takip listesinden çıkarılıyor.",
                            status="success",
                            facts={"Depo": dest, "Mesafe": f"{mil} Mil", "Plan": current_option}
                        )
                        return {"found_target": [{dest:mil}]} # Special signal to STOP
                    
                    # --- PRIORITY 2: MILE LIMIT (COPY CONDITION) ---
                    elif mil < limit_mile:
                        if dest in previously_found:
                            print(f"Skipping {dest} (Already copied)")
                            mgr.add_log(f"Skipping {dest} (Already copied)")
                            found_new["found_new"].append({dest: mil})
                            continue
                        mgr.add_log(f"✅ MESAFE UYGUN: {mil} Mil ({dest})", "success")
                        firsat_sayisi += 1
                        bulunan_firsatlar[current_option] = f"{mil} Mil ➡️ {dest}"
                        found_new["found_new"].append({dest: mil})

                except Exception as e: 
                    mgr.add_log(f"Analiz hatasi: {e}")
                    pass

    # --- SEND SINGLE NOTIFICATION ---
    if bulunan_firsatlar:
        teams_bildirim_gonder(
            mgr=mgr,
            title=f"{firsat_sayisi} Adet Fırsat Bulundu!",
            message=f"**{draft_name}** için aşağıdaki planlar kriterlerinize ({mgr.mile_threshold} mil altı) uyuyor:",
            status="success",
            facts=bulunan_firsatlar # Passes the dictionary we built
        )
        return found_new

    return False

