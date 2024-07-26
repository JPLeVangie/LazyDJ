from flask import Blueprint, render_template, redirect, url_for, request, jsonify, session, current_app, send_from_directory
from app.spotify_utils import get_token, get_spotify_oauth, format_track_info
from app.models import add_recent_track, Track, create_session, get_session, add_track_to_session
import traceback
from app.admin import check_if_admin
import spotipy
from spotipy.exceptions import SpotifyException
import time
import logging
import os
from threading import Lock

bp = Blueprint('routes', __name__)
logger = logging.getLogger(__name__)

# Store recently added tracks
recent_tracks = {}
queue_lock = Lock()

def qr_code_exists():
    """Check if the QR code file exists in the static folder."""
    static_folder = os.path.join(current_app.root_path, 'static')
    qr_code_file = os.path.join(static_folder, 'tip-qr.png')
    return os.path.exists(qr_code_file)

@bp.route('/debug_status')
def debug_status():
    return jsonify({"debug_mode": current_app.debug})

@bp.route('/')
def index():
    current_app.logger.info('Rendering index.html')
    return render_template('index.html')

@bp.route('/login')
def login():
    sp_oauth = get_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@bp.route('/callback')
def callback():
    sp_oauth = get_spotify_oauth()
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)
    session['token_info'] = token_info
    session['token_info']['expires_at'] = int(time.time()) + token_info['expires_in']
    return redirect(url_for('routes.search'))


@bp.route('/search')
def search():
    query = request.args.get('query', '').strip()
    token_info = get_token()
    
    if not token_info:
        return redirect(url_for('routes.login'))

    # If it's not an AJAX request, render the full page
    if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        qr_code_available = qr_code_exists()
        return render_template('search.html', tracks=[], query=query, qr_code_available=qr_code_available)

    try:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        results = sp.search(q=query, type='track', limit=10)
        tracks = results['tracks']['items']

        track_info = []
        for track in tracks:
            track_data = {
                'name': track['name'],
                'artists': ', '.join([artist['name'] for artist in track['artists']]),
                'album_art': track['album']['images'][0]['url'] if track['album']['images'] else None,
                'uri': track['uri'],
                'id': track['id']
            }
            track_info.append(track_data)

        return jsonify({"tracks": track_info})
    except SpotifyException as e:
        error_message = f"Spotify API error: {str(e)}"
        print(error_message)
        print(traceback.format_exc())
        return jsonify({"error": error_message}), 500
    except Exception as e:
        error_message = f"Unexpected error: {str(e)}"
        print(error_message)
        print(traceback.format_exc())
        return jsonify({"error": error_message}), 500


@bp.route('/queue', methods=['POST'])
def queue():
    token_info = get_token()
    if not token_info:
        logger.warning('No token info available')
        return jsonify({"status": "error", "type": "error", "message": "No token info available"}), 401

    track_uri = request.form.get('track_uri')
    track_name = request.form.get('track_name')
    artist_name = request.form.get('artist_name')
    is_admin = request.form.get('is_admin') == 'true'
    current_time = time.time()

    if current_app.debug:
        logger.debug(f"Attempting to queue: {track_name} by {artist_name}")

    if not track_uri:
        logger.error("No track_uri provided in request")
        return jsonify({"status": "error", "type": "error", "message": "No track URI provided"}), 400

    cooldown_period = 1200  # 20 minutes in seconds

    with queue_lock:
        if not is_admin and track_uri in recent_tracks and current_time - recent_tracks[track_uri] < cooldown_period:
            if current_app.debug:
                logger.debug(f"Track on cooldown: {track_name} by {artist_name}")
            return jsonify({"status": "error", "message": "This track was recently played. Please try again later."}), 200

        recent_tracks[track_uri] = current_time
        sp = spotipy.Spotify(auth=token_info['access_token'])
        
        try:
            sp.add_to_queue(track_uri)
            if current_app.debug:
                logger.debug(f"Successfully added to queue: {track_name} by {artist_name}")
            return jsonify({"status": "success", "type": "success", "message": "Track added to queue!"})
        except SpotifyException as e:
            logger.error(f"Spotify API error: {str(e)}")
            if e.http_status == 404 and 'NO_ACTIVE_DEVICE' in str(e):
                return jsonify({"status": "error", "type": "error", "message": "No active device found. Please open Spotify on a device and try again."})
            else:
                return jsonify({"status": "error", "type": "error", "message": f"An error occurred: {str(e)}"})



@bp.route('/play_now', methods=['POST'])
def play_now():
    token_info = get_token()
    if not token_info:
        return jsonify({"status": "error", "message": "Not authenticated"}), 401

    track_uri = request.form.get('track_uri')
    if not track_uri:
        return jsonify({"status": "error", "message": "No track URI provided"}), 400

    sp = spotipy.Spotify(auth=token_info['access_token'])
    
    try:
        # Start playing the track immediately
        sp.start_playback(uris=[track_uri])
        
        # Add the track to the recent tracks
        track_info = sp.track(track_uri)
        add_recent_track(Track(
            uri=track_uri,
            name=track_info['name'],
            artists=', '.join([artist['name'] for artist in track_info['artists']]),
            album_art=track_info['album']['images'][0]['url'] if track_info['album']['images'] else None
        ))
        
        return jsonify({"status": "success", "message": "Track started playing"})
    except SpotifyException as e:
        logger.error(f"Spotify API error: {str(e)}")
        if e.http_status == 404 and 'NO_ACTIVE_DEVICE' in str(e):
            return jsonify({"status": "error", "message": "No active device found. Please open Spotify on a device and try again."}), 404
        else:
            return jsonify({"status": "error", "message": f"An error occurred: {str(e)}"}), 500

@bp.route('/current_queue', methods=['GET'])
def current_queue():
    token_info = get_token()
    if not token_info:
        return jsonify({"error": "Not authenticated"}), 401

    sp = spotipy.Spotify(auth=token_info['access_token'])
    
    try:
        queue_info = sp._get('me/player/queue')
        current_track = sp.currently_playing()

        user_queue = []
        radio_queue = []
        for track in queue_info['queue']:
            track_info = {
                'name': track['name'],
                'artists': ', '.join([artist['name'] for artist in track['artists']]),
                'uri': track['uri']
            }
            if track['uri'] in recent_tracks:
                user_queue.append(track_info)
            else:
                radio_queue.append(track_info)

        current_app.logger.debug(f"Current track: {current_track}")
        current_app.logger.debug(f"User queue: {user_queue}")
        current_app.logger.debug(f"Radio queue: {radio_queue}")

        return jsonify({
            'current_track': {
                'name': current_track['item']['name'],
                'artists': ', '.join([artist['name'] for artist in current_track['item']['artists']])
            } if current_track and current_track['is_playing'] else None,
            'user_queue': user_queue,
            'radio_queue': radio_queue
        })
    except Exception as e:
        current_app.logger.error(f"Error fetching queue: {str(e)}")
        return jsonify({"error": str(e)}), 500

@bp.route('/recommendations', methods=['GET'])
def recommendations():
    token_info = get_token()
    if not token_info:
        return redirect(url_for('routes.index'))

    query = request.args.get('query')
    if not query:
        return jsonify([])

    sp = spotipy.Spotify(auth=token_info['access_token'])
    results = sp.search(q=query, type='track', limit=10)
    tracks = results['tracks']['items']

    track_info = []
    for track in tracks:
        track_data = {
            'name': track['name'],
            'artists': ', '.join([artist['name'] for artist in track['artists']]),
            'album_art': track['album']['images'][0]['url'] if track['album']['images'] else None,
            'uri': track['uri']
        }
        track_info.append(track_data)

    return jsonify(track_info)

@bp.route('/create_session', methods=['POST'])
def create_new_session():
    token_info = get_token()
    if not token_info:
        return jsonify({"error": "Not authenticated"}), 401

    try:
        new_session = create_session(token_info['access_token'])
        session['current_session_id'] = new_session.session_id
        return jsonify({
            "status": "success",
            "session_id": new_session.session_id,
            "redirect_url": url_for('routes.session_view', session_id=new_session.session_id)
        })
    except Exception as e:
        logger.error(f"Error creating new session: {str(e)}")
        return jsonify({"error": str(e)}), 500
    
@bp.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@bp.route('/session/<session_id>')
def session_view(session_id):
    current_session = get_session(session_id)
    if not current_session:
        return render_template('error.html', message="Session not found"), 404

    token_info = get_token()
    if not token_info:
        return redirect(url_for('routes.login'))

    qr_code_available = qr_code_exists()
    return render_template('search.html', session_id=session_id, qr_code_available=qr_code_available)

    return render_template('session.html', session_id=session_id)

@bp.route('/session/<session_id>/search')
def session_search(session_id):
    current_session = get_session(session_id)
    if not current_session:
        return jsonify({"error": "Session not found"}), 404

    query = request.args.get('query', '')
    if not query:
        return jsonify([])

    token_info = get_token()
    if not token_info:
        return jsonify({"error": "Not authenticated"}), 401

    try:
        tracks = search_tracks(query)
        return jsonify(tracks)
    except Exception as e:
        logger.error(f"Error in session search: {str(e)}")
        return jsonify({"error": str(e)}), 500

@bp.route('/session/<session_id>/queue', methods=['POST'])
def add_to_session_queue(session_id):
    current_session = get_session(session_id)
    if not current_session:
        return jsonify({"error": "Session not found"}), 404

    data = request.json
    track_uri = data.get('track_uri')
    track_name = data.get('track_name')
    artist_name = data.get('artist_name')

    if not all([track_uri, track_name, artist_name]):
        return jsonify({"error": "Missing track information"}), 400

    add_track_to_session(current_session, track_uri, track_name, artist_name)
    return jsonify({"status": "success", "message": "Track added to session queue"})

@bp.route('/session/<session_id>/current_queue', methods=['GET'])
def session_current_queue(session_id):
    current_session = get_session(session_id)
    if not current_session:
        return jsonify({"error": "Session not found"}), 404

    token_info = get_token()
    if not token_info:
        return jsonify({"error": "Not authenticated"}), 401

    sp = get_spotify_client()
    if not sp:
        return jsonify({"error": "Failed to get Spotify client"}), 500
    
    try:
        queue_info = sp._get('me/player/queue')
        current_track = sp.currently_playing()

        user_queue = current_session.get_queue()
        radio_queue = [track for track in queue_info['queue'] if track['uri'] not in [t['uri'] for t in user_queue]]

        return jsonify({
            'current_track': {
                'name': current_track['item']['name'],
                'artists': ', '.join([artist['name'] for artist in current_track['item']['artists']])
            } if current_track and current_track['is_playing'] else None,
            'user_queue': user_queue,
            'radio_queue': radio_queue[:5]  # Limit to first 5 tracks
        })
    except Exception as e:
        logger.error(f"Error fetching queue for session {session_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500