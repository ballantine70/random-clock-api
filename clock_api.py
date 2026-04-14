#!/usr/bin/env python3
"""
Random Clock API Server - Poem/1 Compatible
Serves time-synchronized content from Random the Book
Compatible with poem.town Device API spec
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import json
import hashlib
import os
import requests as http_requests
try:
    from zoneinfo import ZoneInfo
    UK_TZ = ZoneInfo('Europe/London')
except ImportError:
    UK_TZ = None

app = Flask(__name__)
CORS(app)  # Enable CORS for browser access

# Configuration
API_KEY = "poem.randombook"  # Change this to your own key
REQUIRE_AUTH = False  # Set to True to require authorization header

# National Rail departures via Realtime Trains API (RTT)
# RTT_TOKEN is the long-lived token from api-portal.rtt.io — used to obtain short-lived access tokens
RTT_TOKEN = os.environ.get('RTT_TOKEN', '')
RTT_TOKEN_URL = 'https://data.rtt.io/api/get_access_token'
RTT_URL = 'https://data.rtt.io/rtt/location'

# ── Admin settings ────────────────────────────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

DEFAULT_SETTINGS = {
    'mode': 'smart',        # random | trains | alternating | smart
    'trainStation': 'HMW',  # 3-letter CRS code
}

AVAILABLE_MODES = [
    {
        'id': 'random',
        'name': 'Random Clock',
        'description': 'Always shows random content from the book',
    },
    {
        'id': 'trains',
        'name': 'Train Departures',
        'description': 'Always shows upcoming train departures',
    },
    {
        'id': 'alternating',
        'name': 'Alternating',
        'description': 'Switches between random and trains each minute',
    },
    {
        'id': 'smart',
        'name': 'Smart (commute)',
        'description': 'Trains during commute hours, random clock otherwise',
    },
]

_settings_cache = None

def load_settings():
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    try:
        with open(SETTINGS_FILE) as f:
            _settings_cache = {**DEFAULT_SETTINGS, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        _settings_cache = DEFAULT_SETTINGS.copy()
    return _settings_cache

def save_settings(updates):
    global _settings_cache
    current = load_settings()
    _settings_cache = {**current, **updates}
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(_settings_cache, f, indent=2)
    return _settings_cache

# In-memory access token cache
_rtt_access_token = None
_rtt_access_token_expiry = None

def get_rtt_access_token():
    """Exchange the long-lived RTT token for a short-lived access token (cached)."""
    global _rtt_access_token, _rtt_access_token_expiry
    now = datetime.now()
    if _rtt_access_token and _rtt_access_token_expiry and now < _rtt_access_token_expiry:
        return _rtt_access_token, None
    try:
        resp = http_requests.post(
            RTT_TOKEN_URL,
            headers={'Authorization': f'Bearer {RTT_TOKEN}'},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get('token') or data.get('access_token') or data.get('accessToken')
        if not token:
            return None, f'Unexpected token response: {data}'
        _rtt_access_token = token
        _rtt_access_token_expiry = now + timedelta(minutes=50)
        return token, None
    except http_requests.exceptions.RequestException as exc:
        return None, str(exc)

# Commute windows (inclusive, UK local time) — (start_hour, start_min, end_hour, end_min)
COMMUTE_WINDOWS = [
    (6,  0,  9, 30),   # morning:  06:00 – 09:30
    (16, 0, 19, 30),   # evening:  16:00 – 19:30
]

def is_commute_time(hour, minute):
    """Return True if hour:minute (UK local) falls within a commute window."""
    t = hour * 60 + minute
    return any(
        sh * 60 + sm <= t <= eh * 60 + em
        for sh, sm, eh, em in COMMUTE_WINDOWS
    )

# Load the content database
with open('random_clock_content.json', 'r') as f:
    data = json.load(f)
    ITEMS = data['items']

class SeededRandom:
    """Seeded random number generator for consistent daily shuffles"""
    def __init__(self, seed):
        self.seed = seed
    
    def next(self):
        self.seed = (self.seed * 9301 + 49297) % 233280
        return self.seed / 233280

def get_today_seed():
    """Get today's date as seed (YYYYMMDD format) in UK local time"""
    now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
    return int(now.strftime('%Y%m%d'))

def shuffle_with_seed(array, seed):
    """Shuffle array using seeded random"""
    rng = SeededRandom(seed)
    shuffled = array.copy()
    
    for i in range(len(shuffled) - 1, 0, -1):
        j = int(rng.next() * (i + 1))
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
    
    return shuffled

def generate_daily_schedule():
    """Generate daily schedule: each item appears 3 times"""
    seed = get_today_seed()
    
    # Create array with each item appearing 3 times
    tripled = []
    for item in ITEMS:
        tripled.extend([item, item, item])
    
    # Shuffle the tripled array with today's seed
    return shuffle_with_seed(tripled, seed)

def get_current_minute():
    """Get current minute of day (0-1439) in UK local time"""
    now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
    return now.hour * 60 + now.minute

def get_train_departures():
    """Fetch departures from Hampton Wick in the next 30 minutes via RTT API.
    Returns (list_of_trains, error_string_or_None).
    """
    if not RTT_TOKEN:
        return [], 'RTT_TOKEN environment variable is not set'

    access_token, err = get_rtt_access_token()
    if not access_token:
        return [], f'Failed to obtain RTT access token: {err}'

    try:
        station = load_settings().get('trainStation', 'HMW').upper()
        resp = http_requests.get(
            RTT_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            params={'code': f'gb-nr:{station}', 'timeWindow': 30},
            timeout=8,
        )
        resp.raise_for_status()
        all_services = resp.json().get('services') or []
    except http_requests.exceptions.RequestException as exc:
        return [], str(exc)

    def parse_iso_dt(iso_str):
        """Parse ISO 8601 string from RTT — times are already UK local, ignore timezone marker."""
        if not iso_str:
            return None
        try:
            # RTT returns UK local time but marks it as Z (UTC) — strip timezone
            # info entirely and use the wall-clock value directly.
            dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
            return dt.replace(tzinfo=None)
        except (ValueError, AttributeError):
            return None

    trains = []
    for svc in all_services:
        temporal = svc.get('temporalData') or {}
        departure = temporal.get('departure')
        if not departure:
            continue  # pass-through, not a stopping service

        cancelled = departure.get('isCancelled', False)
        sched_dt  = parse_iso_dt(departure.get('scheduleAdvertised'))
        est_dt    = parse_iso_dt(departure.get('realtimeEstimate'))

        scheduled = sched_dt.strftime('%H:%M') if sched_dt else '?'

        if cancelled:
            display_time = 'CANC'
            delay_mins   = 0
        elif est_dt and sched_dt:
            delay_mins   = max(0, int((est_dt - sched_dt).total_seconds() / 60))
            display_time = est_dt.strftime('%H:%M') if delay_mins else scheduled
        else:
            display_time = scheduled
            delay_mins   = 0

        destinations = svc.get('destination') or []
        destination = destinations[0].get('location', {}).get('description', 'Unknown') if destinations else 'Unknown'

        loc_meta = svc.get('locationMetadata') or {}
        plat = loc_meta.get('platform') or {}
        platform = plat.get('actual') or plat.get('planned')
        platform_display = f'Plat {platform}' if platform else 'Plat ?'

        if cancelled:
            status = 'Cancelled'
        elif delay_mins:
            status = f'+{delay_mins}m late'
        else:
            status = 'On time'

        trains.append({
            'time': display_time,
            'destination': destination,
            'platform': platform_display,
            'scheduled': scheduled,
            'delay_mins': delay_mins,
            'status': status,
        })

    return trains, None


def format_trains_poem(trains, time24):
    """Format a list of train departures as a poem string for Poem/1 devices."""
    if not trains:
        return f"{time24} — No departures in next 30 min from Hampton Wick"

    lines = [f"{time24} — Hampton Wick departures"]
    for t in trains:
        dest   = t['destination'][:18].ljust(18)
        status = t['status']
        lines.append(f"{t['time']}  {dest}  {t['platform']}  {status}")
    return '\n'.join(lines)


def check_auth():
    """Check authorization header"""
    if not REQUIRE_AUTH:
        return True
    
    auth_header = request.headers.get('Authorization', '')
    expected = f'Bearer {API_KEY}'
    return auth_header == expected

def generate_poem_id(time24, content):
    """Generate a unique poem ID based on time and content"""
    combined = f"{time24}-{content}"
    return hashlib.md5(combined.encode()).hexdigest()[:8]

# Poem/1 Device API Endpoints

@app.route('/api/v1/clock/status', methods=['POST'])
def status():
    """Status endpoint - does not require auth"""
    try:
        body = request.get_json(force=True, silent=True) or {}
    except:
        body = {}
    
    screen_id = body.get('screenId', 'unknown')
    build_id = body.get('buildId')
    
    now = datetime.utcnow()
    
    return jsonify({
        'success': True,
        'device': {
            'screenId': screen_id,
            'buildId': build_id,
            'lastSeen': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'seen': 1,
            'createdAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'isClaimed': False
        }
    })

@app.route('/api/v1/clock/compose', methods=['POST'])
def compose():
    """Main compose endpoint - returns poem for current time"""
    if REQUIRE_AUTH and not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        body = request.get_json(force=True, silent=True) or {}
    except:
        body = {}
    
    # Extract time from request
    if 'time24' in body:
        time24 = body['time24']
        # Parse HH:MM to get minute of day
        hours, mins = map(int, time24.split(':'))
        minute = hours * 60 + mins
    elif 'geolocate' in body:
        # Parse ISO datetime and extract time
        dt = datetime.fromisoformat(body['geolocate'].replace('Z', '+00:00'))
        # Convert to local time (you may want to handle timezone properly)
        time24 = dt.strftime('%H:%M')
        minute = dt.hour * 60 + dt.minute
    else:
        # Default to current time
        now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
        time24 = now.strftime('%H:%M')
        minute = get_current_minute()
    
    # Get content for this minute
    schedule = generate_daily_schedule()
    current_item = schedule[minute]
    
    # Replace en-dashes in content with regular hyphens (e-ink wrapping issue)
    content = current_item['content'].replace('–', '-')
    
    # Format as poem with time prepended
    poem = f"{time24} — {content}"
    
    # Generate poem ID
    poem_id = generate_poem_id(time24, poem)
    
    response = {
        'poemId': poem_id,
        'time24': time24,
        'poem': poem,
        'preferredFont': 'INTER',
        'screensaver': False
    }
    
    return jsonify(response)

@app.route('/api/v1/clock/notes/<note_id>/seen', methods=['POST'])
def mark_note_seen(note_id):
    """Mark a note as seen"""
    if REQUIRE_AUTH and not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    body = request.get_json()
    screen_id = body.get('screenId')
    
    # For now, always return success
    # In a full implementation, you'd store this in a database
    return jsonify({'success': True})

@app.route('/api/v1/clock/likes/<poem_id>/mark', methods=['POST'])
def like_poem(poem_id):
    """Mark a poem as liked"""
    if REQUIRE_AUTH and not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    body = request.get_json()
    screen_id = body.get('screenId')
    
    # For now, always return success
    # In a full implementation, you'd store this in a database
    return jsonify({'success': True})

@app.route('/api/v1/clock/likes/<poem_id>/unmark', methods=['POST'])
def unlike_poem(poem_id):
    """Remove like from a poem"""
    if REQUIRE_AUTH and not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    body = request.get_json()
    screen_id = body.get('screenId')
    
    # For now, always return success
    # In a full implementation, you'd store this in a database
    return jsonify({'success': True})

# Additional convenience endpoints (not part of Poem/1 spec)


@app.route('/api/v1/clock', methods=['GET'])
def clock_get():
    """Convenience GET endpoint - returns current content"""
    schedule = generate_daily_schedule()
    minute = get_current_minute()
    current_item = schedule[minute]
    
    now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
    time24 = now.strftime('%H:%M')

    return jsonify({
        'time': time24,
        'content': current_item['content'],
        'card': current_item['card'],
        'minute': minute,
        'total_minutes': 1440,
        'timestamp': now.isoformat()
    })

@app.route('/api/v1/clock/minute/<int:minute>', methods=['GET'])
def clock_at_minute(minute):
    """Get content for specific minute (for testing)"""
    if minute < 0 or minute >= 1440:
        return jsonify({'error': 'Minute must be between 0 and 1439'}), 400
    
    schedule = generate_daily_schedule()
    item = schedule[minute]
    
    hours = minute // 60
    mins = minute % 60
    time_str = f'{hours:02d}:{mins:02d}'
    
    return jsonify({
        'time': time_str,
        'content': item['content'],
        'card': item['card'],
        'minute': minute,
        'total_minutes': 1440
    })

@app.route('/api/v1/clock/stats', methods=['GET'])
def stats():
    """Return statistics about the content database"""
    return jsonify({
        'total_items': len(ITEMS),
        'total_cards': len(ITEMS) // 4,
        'appearances_per_day': len(ITEMS) * 3,
        'minutes_per_day': 1440,
        'coverage': f'{(len(ITEMS) * 3 / 1440) * 100:.1f}%'
    })

@app.route('/api/v1/trains/compose', methods=['POST'])
def trains_compose():
    """Poem/1 compatible endpoint — returns next 4 Hampton Wick departures."""
    if REQUIRE_AUTH and not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}

    # Determine display time (same logic as /compose)
    if 'time24' in body:
        time24 = body['time24']
    elif 'geolocate' in body:
        dt = datetime.fromisoformat(body['geolocate'].replace('Z', '+00:00'))
        time24 = dt.strftime('%H:%M')
    else:
        now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
        time24 = now.strftime('%H:%M')

    trains, error = get_train_departures()

    poem = format_trains_poem(trains, time24)
    poem_id = generate_poem_id(time24, poem)

    response = {
        'poemId': poem_id,
        'time24': time24,
        'poem': poem,
        'preferredFont': 'INTER',
        'screensaver': False,
        'trains': trains,
    }
    if error:
        response['error'] = error

    return jsonify(response)


@app.route('/api/v1/compose', methods=['POST'])
def smart_compose():
    """Combined Poem/1 endpoint — trains during commute hours, clock otherwise."""
    if REQUIRE_AUTH and not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}

    # Determine UK local time from the request
    if 'time24' in body:
        time24 = body['time24']
        hour, minute = map(int, time24.split(':'))
    elif 'geolocate' in body:
        dt = datetime.fromisoformat(body['geolocate'].replace('Z', '+00:00'))
        if UK_TZ:
            dt = dt.astimezone(UK_TZ)
        time24 = dt.strftime('%H:%M')
        hour, minute = dt.hour, dt.minute
    else:
        now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
        time24 = now.strftime('%H:%M')
        hour, minute = now.hour, now.minute

    mode = load_settings().get('mode', 'smart')
    minute_of_day = hour * 60 + minute

    show_trains = (
        mode == 'trains'
        or (mode == 'alternating' and minute_of_day % 2 == 1)
        or (mode == 'smart' and is_commute_time(hour, minute))
    )

    if show_trains:
        trains, error = get_train_departures()
        poem = format_trains_poem(trains, time24)
        poem_id = generate_poem_id(time24, poem)
        response = {
            'poemId': poem_id,
            'time24': time24,
            'poem': poem,
            'preferredFont': 'INTER',
            'screensaver': False,
            'mode': 'trains',
            'trains': trains,
        }
        if error:
            response['error'] = error
    else:
        schedule = generate_daily_schedule()
        content = schedule[minute_of_day]['content'].replace('–', '-')
        poem = f"{time24} — {content}"
        poem_id = generate_poem_id(time24, poem)
        response = {
            'poemId': poem_id,
            'time24': time24,
            'poem': poem,
            'preferredFont': 'INTER',
            'screensaver': False,
            'mode': 'clock',
        }

    return jsonify(response)


@app.route('/api/v1/trains', methods=['GET'])
def trains_get():
    """Convenience GET endpoint — returns live Hampton Wick departures."""
    trains, error = get_train_departures()

    now = datetime.now(UK_TZ) if UK_TZ else datetime.now()
    time24 = now.strftime('%H:%M')

    response = {
        'time': time24,
        'station': 'Hampton Wick',
        'trains': trains,
        'timestamp': now.isoformat(),
    }
    if error:
        response['error'] = error

    return jsonify(response)


@app.route('/api/v1/admin/settings', methods=['GET'])
def admin_get_settings():
    settings = load_settings()
    return jsonify({**settings, 'availableModes': AVAILABLE_MODES})

@app.route('/api/v1/admin/settings', methods=['POST'])
def admin_save_settings():
    body = request.get_json(force=True, silent=True) or {}
    allowed = {'mode', 'trainStation'}
    updates = {k: v for k, v in body.items() if k in allowed}
    if 'mode' in updates and updates['mode'] not in {m['id'] for m in AVAILABLE_MODES}:
        return jsonify({'error': f"Unknown mode: {updates['mode']}"}), 400
    if 'trainStation' in updates:
        updates['trainStation'] = updates['trainStation'].upper().strip()[:3]
    saved = save_settings(updates)
    return jsonify({**saved, 'availableModes': AVAILABLE_MODES})

@app.route('/admin', methods=['GET'])
def admin():
    """Admin control panel"""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'admin.html')

@app.route('/simulator', methods=['GET'])
def simulator():
    """Poem/1 device simulator for testing the API"""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'simulator.html')


@app.route('/', methods=['GET'])
def index():
    """Simple index page with API documentation"""
    return '''
    <html>
    <head>
        <title>Random Clock API - Poem/1 Compatible</title>
        <style>
            body { font-family: monospace; padding: 40px; max-width: 900px; margin: 0 auto; }
            code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }
            pre { background: #f0f0f0; padding: 15px; border-radius: 5px; overflow-x: auto; }
            h2 { margin-top: 30px; border-bottom: 2px solid #333; padding-bottom: 5px; }
            .endpoint { background: #e8f4f8; padding: 10px; margin: 10px 0; border-left: 4px solid #0066cc; }
        </style>
    </head>
    <body>
        <h1>Random Clock API</h1>
        <p>Compatible with <a href="https://poem.town/developer/device-api">Poem/1 Device API</a></p>
        
        <h2>Poem/1 Compatible Endpoints:</h2>
        
        <div class="endpoint">
            <strong>POST /api/v1/clock/status</strong><br>
            Check device status (no auth required)
        </div>
        
        <div class="endpoint">
            <strong>POST /api/v1/clock/compose</strong><br>
            Get poem for current time
        </div>
        
        <div class="endpoint">
            <strong>POST /api/v1/clock/notes/{noteId}/seen</strong><br>
            Mark note as seen
        </div>
        
        <div class="endpoint">
            <strong>POST /api/v1/clock/likes/{poemId}/mark</strong><br>
            Like a poem
        </div>
        
        <div class="endpoint">
            <strong>POST /api/v1/clock/likes/{poemId}/unmark</strong><br>
            Unlike a poem
        </div>
        
        <h2>Smart Combined Mode (recommended for devices):</h2>

        <div class="endpoint">
            <strong>POST /api/v1/compose</strong><br>
            Trains during commute hours (06:00–09:30 and 16:00–19:30 UK time),
            random clock content at all other times. Point your device here.
        </div>

        <h2>Individual Mode Endpoints:</h2>

        <div class="endpoint">
            <strong>POST /api/v1/trains/compose</strong><br>
            Always returns next 4 departures from Hampton Wick (via National Rail Darwin).
            Set <code>TFL_API_KEY</code> env var for higher rate limits.
        </div>

        <div class="endpoint">
            <strong>GET /api/v1/trains</strong><br>
            Live departure board for Hampton Wick (convenience endpoint).
        </div>

        <h2>Convenience Endpoints (for testing):</h2>
        <ul>
            <li><a href="/api/v1/clock">/api/v1/clock</a> - Current minute's content (GET)</li>
            <li><a href="/api/v1/clock/minute/720">/api/v1/clock/minute/720</a> - Content for minute 720 (GET)</li>
            <li><a href="/api/v1/clock/stats">/api/v1/clock/stats</a> - Database statistics (GET)</li>
            <li><a href="/api/v1/trains">/api/v1/trains</a> - Live Hampton Wick departures (GET)</li>
        </ul>
        
        <h2>Test /compose endpoint:</h2>
        <pre>curl -X POST \\
  -H "Content-Type: application/json" \\
  -d '{"time24": "12:34"}' \\
  http://localhost:5000/api/v1/clock/compose</pre>
        
        <h2>Current Content:</h2>
        <div id="content" style="margin-top: 20px; padding: 20px; background: #f0f0f0; border-radius: 5px;"></div>
        
        <script>
            // Test the compose endpoint
            fetch('/api/v1/clock/compose', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ geolocate: new Date().toISOString() })
            })
            .then(r => r.json())
            .then(data => {
                document.getElementById('content').innerHTML = 
                    '<strong>' + data.time24 + '</strong><br><br>' +
                    '<div style="font-size: 18px; line-height: 1.6;">' + data.poem + '</div><br>' +
                    '<em style="color: #666;">Poem ID: ' + data.poemId + '</em>';
            })
            .catch(err => {
                document.getElementById('content').innerHTML = 
                    '<span style="color: red;">Error: ' + err + '</span>';
            });
        </script>
    </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print('Starting Random Clock API server (Poem/1 compatible)...')
    print(f'Port: {port}')
    print(f'Auth required: {REQUIRE_AUTH}')
    if REQUIRE_AUTH:
        print(f'API key: {API_KEY}')
    app.run(debug=False, host='0.0.0.0', port=port)
