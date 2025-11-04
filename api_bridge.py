from flask import Flask, request, jsonify
import requests
import json
import time
from datetime import datetime, timedelta, timezone
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# --- Configuration ---
# Your Dolibarr API settings
DOLIBARR_URL = "https://bsw.com.ng/hr/api/index.php"
DOLIBARR_API_KEY = "3j4mnvNJuS9MH8Sm440XNAeaY9fGz19k"  # Generate a key for a dedicated API user in Dolibarr

# Secure keys for staff members (MUST match the keys used in generate_qr_codes.py)
STAFF_KEYS = {
     5:"1462b9a6a849086c",
     7:"051fcb313dadf30d",
     8:"051fcb313dadf30d",
}

# --- Staff Group Mapping ---
# Maps each user ID to a defined group name
STAFF_GROUPS = {
    5: 'FULL_TIME_A',
    7: 'FULL_TIME_A',
    8: 'FULL_TIME_A',
    102: 'PART_TIME_B',
    103: 'FULL_TIME_A', # User 103 is also in Group A
}

# --- Group Schedules (Day of Week: MON, TUE, WED, THU, FRI, SAT, SUN) ---
# Times are in 24-hour format (HH:MM)
# clock_in_window: minutes *before* start_time allowed for clock-in
# clock_out_window: minutes *after* end_time allowed for clock-out
GROUP_SCHEDULES = {
    'FULL_TIME_A': {
        'MON': {'start_time': '00:00', 'end_time': '23:59', 'clock_in_window': 15, 'clock_out_window': 30},
        'TUE': {'start_time': '00:00', 'end_time': '23:59', 'clock_in_window': 15, 'clock_out_window': 30},
        'WED': {'start_time': '00:00', 'end_time': '23:59', 'clock_in_window': 15, 'clock_out_window': 30},
        'THU': {'start_time': '00:00', 'end_time': '23:59', 'clock_in_window': 15, 'clock_out_window': 30},
        'FRI': {'start_time': '00:00', 'end_time': '23:59', 'clock_in_window': 15, 'clock_out_window': 30},
    },
    'PART_TIME_B': {
        'MON': {'start_time': '10:00', 'end_time': '14:00', 'clock_in_window': 10, 'clock_out_window': 10},
        'WED': {'start_time': '10:00', 'end_time': '14:00', 'clock_in_window': 10, 'clock_out_window': 10},
    },
    # Add more groups as needed (e.g., 'WEEKEND_SHIFT', 'ADMIN')
}

# Custom Dolibarr Agenda Event Types (MUST be configured in Dolibarr Dictionaries)
CLOCK_IN_TYPE = 'AC_CLOIN'
CLOCK_OUT_TYPE = 'AC_CLOOUT'
# --- End Configuration ---

def dolibarr_api_call(method, endpoint, data=None):
    """Generic function to handle Dolibarr REST API calls."""
    headers = {
        "DOLAPIKEY": DOLIBARR_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    url = f"{DOLIBARR_URL}/{endpoint}"
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=data)
        elif method == 'POST':
            response = requests.post(url, headers=headers, data=json.dumps(data))
        else:
            return None, 405 # Method Not Allowed

        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        return response.json(), response.status_code
    except requests.exceptions.RequestException as e:
        print(f"Dolibarr API Error: {e}")
        return {"error": str(e)}, 500

def get_last_clock_action(user_id):
    """Queries Dolibarr to find the last clock-in/out action for a user."""
    # Filter for the user and only the clock-in/out event types
    sqlfilters = f"(t.fk_user: =:'{user_id}') AND (t.type: =:'{CLOCK_IN_TYPE}' OR t.type: =:'{CLOCK_OUT_TYPE}')"
    
    # Get the latest event (sort by date descending, limit 1)
    data = {
        "sortfield": "t.dateo",
        "sortorder": "DESC",
        "limit": 1,
        "sqlfilters": sqlfilters
    }
    
    events, status = dolibarr_api_call('GET', 'agendaevents', data=data)
    
    if status == 200 and events:
        # The API returns a list, we take the first element (the latest)
        return events[0] # Return the full event object
    
    # If no events found or error, return None
    return None

def get_schedule_for_today(user_id, current_time):
    """Retrieves the schedule for the user for the current day."""
    # Dolibarr uses 0=Mon, 6=Sun. Python uses 0=Mon, 6=Sun for weekday().
    day_map = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
    day_of_week = day_map[current_time.weekday()]
    
    # 1. Get the user's group
    group_name = STAFF_GROUPS.get(user_id)
    if not group_name:
        return None, f"User {user_id} is not assigned to a staff group."
        
    # 2. Get the schedule for that group
    group_schedule = GROUP_SCHEDULES.get(group_name)
    if not group_schedule:
        return None, f"Staff group '{group_name}' has no defined schedule."
        
    # 3. Get the schedule for the current day
    schedule = group_schedule.get(day_of_week)
    
    if not schedule:
        return None, f"User {user_id} (Group: {group_name}) is not scheduled to work on {day_of_week}."

    # Parse scheduled times
    try:
        start_time_str = schedule['start_time']
        end_time_str = schedule['end_time']
        
        # Combine current date with scheduled time
        scheduled_start = current_time.replace(hour=int(start_time_str[:2]), minute=int(start_time_str[3:]), second=0, microsecond=0)
        scheduled_end = current_time.replace(hour=int(end_time_str[:2]), minute=int(end_time_str[3:]), second=0, microsecond=0)
        
        # Calculate allowed windows
        allowed_in_start = scheduled_start - timedelta(minutes=schedule['clock_in_window'])
        allowed_out_end = scheduled_end + timedelta(minutes=schedule['clock_out_window'])
        
        return {
            'scheduled_start': scheduled_start,
            'scheduled_end': scheduled_end,
            'allowed_in_start': allowed_in_start,
            'allowed_out_end': allowed_out_end,
            'clock_in_window': schedule['clock_in_window']
        }, None
    except Exception as e:
        return None, f"Error parsing schedule times: {e}"

@app.route('/clock', methods=['POST'])
def clock_action():
    """Handles the clock-in/clock-out request from the terminal."""
    data = request.get_json()
    user_id = data.get('user_id')
    key = data.get('key')
    timestamp = data.get('timestamp')

    # 1. Basic input validation
    if not all([user_id, key, timestamp]):
        return jsonify({"error": "Missing user_id, key, or timestamp"}), 400

    # 2. Security Check: Verify the secure key
    if user_id not in STAFF_KEYS or STAFF_KEYS[user_id] != key:
        return jsonify({"error": "Invalid user ID or secure key"}), 403

    # --- NEW LOGIC START ---
    
    # Convert Unix timestamp to datetime object (assuming UTC for consistency)
    # NOTE: The client (HTML) sends a Unix timestamp. Python's datetime.fromtimestamp assumes local time 
    # unless tzinfo is provided. We use UTC for server-side consistency.
    current_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    
    # 3. Schedule Validation
    schedule_data, error = get_schedule_for_today(user_id, current_time)
    if error:
        return jsonify({"error": error}), 403 # Forbidden due to no schedule

    # 4. Determine Action (In or Out)
    last_event = get_last_clock_action(user_id)
    last_action_type = last_event['type'] if last_event else None
    
    if last_action_type == CLOCK_IN_TYPE:
        # Last action was IN, so next action is OUT
        action_type = CLOCK_OUT_TYPE
        action_label = "Clock-out"
    else:
        # Last action was OUT, or no action yet, so next action is IN
        action_type = CLOCK_IN_TYPE
        action_label = "Clock-in"

    # 5. Duplicate Clock-in/out Prevention
    if last_event and last_action_type == action_type:
        # Check if the last action was less than 1 minute ago (to prevent accidental double-taps)
        last_event_time = datetime.fromtimestamp(last_event['dateo'], tz=timezone.utc)
        if (current_time - last_event_time).total_seconds() < 60:
            return jsonify({"error": f"Duplicate clocking attempt. Last {action_label} was less than 1 minute ago."}), 409 # Conflict
        
        # If the last action was the same type, but more than 1 minute ago, 
        # it means the user forgot to clock out/in. We reject the action.
        return jsonify({"error": f"Cannot {action_label}. You must {'Clock-out' if action_type == CLOCK_IN_TYPE else 'Clock-in'} first."}), 409

    # 6. Time Window Check and Late Arrival Flagging
    note_suffix = ""
    
    if action_type == CLOCK_IN_TYPE:
        # Check Clock-in Window
        if current_time < schedule_data['allowed_in_start']:
            return jsonify({"error": f"Clock-in too early. Allowed from {schedule_data['allowed_in_start'].strftime('%H:%M')}."}), 403
        
        # Check for Late Arrival (5 minute grace period)
        late_threshold = schedule_data['scheduled_start'] + timedelta(minutes=5)
        if current_time > late_threshold:
            note_suffix = f" **LATE ARRIVAL** (Scheduled: {schedule_data['scheduled_start'].strftime('%H:%M')})"
            
    elif action_type == CLOCK_OUT_TYPE:
        # Check Clock-out Window (Allow clock-out anytime after scheduled start, but check end window)
        if current_time > schedule_data['allowed_out_end']:
            # Reject if clock-out is too late based on the window
            return jsonify({"error": f"Clock-out too late. Allowed until {schedule_data['allowed_out_end'].strftime('%H:%M')}."}), 403
        
        # Optional: Flag Early Departure
        if current_time < schedule_data['scheduled_end']:
             note_suffix = f" **EARLY DEPARTURE** (Scheduled: {schedule_data['scheduled_end'].strftime('%H:%M')})"
             # We allow early departure but flag it.
             
    # --- NEW LOGIC END ---

    # 7. Prepare Dolibarr Payload
    payload = {
        "type": action_type,
        "dateo": timestamp, # Start date/time (Unix timestamp)
        "datef": timestamp, # End date/time (same for a single clock action)
        "label": action_label,
        "note": f"{action_label} via QR Terminal. Terminal ID: {request.remote_addr}{note_suffix}",
        "fk_user": user_id,
        "fullday": 0 # Not a full day event
    }

    # 8. Send to Dolibarr API
    result, status = dolibarr_api_call('POST', 'agendaevents', data=payload)

    if status == 200 and isinstance(result, int):
        # Successful POST returns the new object ID
        return jsonify({
            "success": True,
            "action": action_label,
            "user_id": user_id,
            "event_id": result,
            "message": f"Clock {action_label} recorded. Status: {note_suffix.strip() or 'On Time'}"
        }), 200
    else:
        # API call failed
        return jsonify({
            "success": False,
            "error": "Dolibarr API call failed",
            "details": result
        }), status

if __name__ == '__main__':
    # WARNING: Do not use the Flask development server in production.
    # Use a proper WSGI server like Gunicorn or uWSGI.
    app.run(host='0.0.0.0', port=5000, debug=True)
