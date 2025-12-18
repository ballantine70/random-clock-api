#!/usr/bin/env python3
"""
Random Clock API Server - Poem/1 Compatible
Serves time-synchronized content from Random the Book
Compatible with poem.town Device API spec
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
import json
import hashlib

app = Flask(__name__)
CORS(app)  # Enable CORS for browser access

# Configuration
API_KEY = "poem.randombook"  # Change this to your own key
REQUIRE_AUTH = False  # Set to True to require authorization header

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
    """Get today's date as seed (YYYYMMDD format)"""
    now = datetime.now()
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
    """Get current minute of day (0-1439)"""
    now = datetime.now()
    return now.hour * 60 + now.minute

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
        now = datetime.now()
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
    
    now = datetime.now()
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
        
        <h2>Convenience Endpoints (for testing):</h2>
        <ul>
            <li><a href="/api/v1/clock">/api/v1/clock</a> - Current minute's content (GET)</li>
            <li><a href="/api/v1/clock/minute/720">/api/v1/clock/minute/720</a> - Content for minute 720 (GET)</li>
            <li><a href="/api/v1/clock/stats">/api/v1/clock/stats</a> - Database statistics (GET)</li>
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
    import os
    port = int(os.environ.get('PORT', 5000))
    print('Starting Random Clock API server (Poem/1 compatible)...')
    print(f'Port: {port}')
    print(f'Auth required: {REQUIRE_AUTH}')
    if REQUIRE_AUTH:
        print(f'API key: {API_KEY}')
    app.run(debug=False, host='0.0.0.0', port=port)
