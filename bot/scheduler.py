import time

from bot.auth import switch_account_backend
from bot.drafts import drafti_planla_backend

def safe_run(manager):
    try:
        gorev(manager)
    except Exception as e:
        manager.add_log(f"🔥 Scheduler crash: {e}", "error")

def gorev(mgr):
    if not mgr.is_running: return
    if not mgr.watch_list: return

    mgr.add_log(f"⏰ Periyodik kontrol başladı. ({len(mgr.watch_list)} adet)", "info")
    
    tasks = list(mgr.watch_list.values())
    sorted_tasks = sorted(tasks, key=lambda x: x.get('account_id', ''))
    
    #keys_to_remove = []

    for item in sorted_tasks:
        d_key = item['date'] 
        d_name = item['name']
        d_account = item['account_name']
        
        # --- CONTEXT SWITCHING ---
        target_acc_id = item.get('account_id')
        target_acc_name = item.get('account_name', 'Bilinmiyor')
        
        if target_acc_id and target_acc_id != mgr.current_account_id:
            if switch_account_backend(mgr, target_acc_id):
                mgr.current_account_id = target_acc_id
                mgr.current_account_name = target_acc_name
                time.sleep(2)
            else:
                continue

        # --- EXECUTE (Just pass the item!) ---
        sonuc = drafti_planla_backend(mgr, item)
        
        # --- UPDATE LOGIC ---
        if isinstance(sonuc, dict) and 'STOP' in sonuc:
            new_found_list = sonuc.pop("STOP")
            mgr.add_history_entry(d_name, new_found_list, d_account)
            if d_key in mgr.watch_list:
                del mgr.watch_list[d_key]
            
        elif isinstance(sonuc, dict):
            new_key = sonuc['date']
            
            # 1. Update Memory
            
            new_found_list = sonuc.pop('newly_found_warehouse', [])
            known_wh = item.get('found_warehouses', []).copy()

            added_new_unique = False
            
            # Loop through the list of dicts (e.g. [{'AVP1': 100}, {'MEM1': 200}])
            if new_found_list:
                for data_item in new_found_list:
                    # Extract Key (Warehouse Name)
                    if isinstance(data_item, dict):
                        wh_name = next(iter(data_item)) 
                    else:
                        wh_name = str(data_item)
                        
                    # Check duplication
                    if wh_name not in known_wh:
                        known_wh.append(wh_name)
                        added_new_unique = True
            
            # 2. Update HISTORY (Display - Full dicts)
            # Only add to history table if we found something new
            if added_new_unique:
                mgr.add_history_entry(d_name, new_found_list, d_account)

            # 2. Transfer Metadata
            sonuc['found_warehouses'] = known_wh
            sonuc['account_id'] = target_acc_id
            sonuc['account_name'] = target_acc_name
            sonuc['max_mile'] = item.get('max_mile')
            sonuc['targets'] = item.get('targets')

            if new_key != d_key:
                if d_key in mgr.watch_list:
                    del mgr.watch_list[d_key]
                mgr.watch_list[new_key] = sonuc
            else:
                mgr.watch_list[d_key] = sonuc