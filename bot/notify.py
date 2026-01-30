import requests


def teams_bildirim_gonder(mgr, title, message, facts=None, status="info"):
    """
    Sends a high-contrast Adaptive Card with dividers between items.
    """
    # 1. Color and Icon Logic
    if not mgr.teams_webhook_url:
        return False
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
        response = mgr.session.post(mgr.teams_webhook_url, json=payload, timeout=10)
        if response.status_code not in [200, 202]:
            print(f"❌ Teams Hatası: {response.status_code}")
    except Exception as e:
        print(f"❌ Teams Bağlantı Hatası: {e}")

