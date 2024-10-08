# routes.py

from flask import Blueprint, render_template, redirect, url_for, request, jsonify, session, current_app
from app.spotify_utils import get_token, get_spotify_oauth, format_track_info, get_spotify_client
from app.models import add_recent_track, Track, add_track_to_session, get_session, delete_session
from app.admin import check_if_admin
from app.sessions import create_new_session
from app.sessions import bp as sessions_bp
from .log_utils import format_debug_output

import spotipy
from spotipy.exceptions import SpotifyException
import time
import logging
import os
from threading import Lock
import traceback
import json

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
    try:
        token_info = sp_oauth.get_access_token(code)
        session['token_info'] = json.dumps(token_info)
        logger.info("Token info stored in session")
        logger.debug(f"Token info: {json.dumps(token_info)}")
        return redirect(url_for('routes.search'))
    except spotipy.oauth2.SpotifyOauthError as e:
        logger.error(f"Spotify OAuth Error: {e}")
        if 'invalid_grant' in str(e):
            # Clear the session and redirect to login
            session.clear()
            return redirect(url_for('routes.login'))
    except Exception as e:
        logger.error(f"Error in callback: {e}")
        return jsonify({"error": "Authentication failed"}), 400

@bp.route('/search')
def search():
    query = request.args.get('query', '').strip()
    token_info = get_token()
    
    if not token_info:
        return redirect(url_for('routes.login'))

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
        logger.error(f"Spotify API error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Spotify API error: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@bp.route('/queue', methods=['POST'])
def queue():
    logger.debug("Entering queue route")
    token_info = get_token()
    if not token_info:
        logger.warning('No token info available')
        return jsonify({"status": "error", "type": "error", "message": "No token info available"}), 401

    track_uri = request.form.get('track_uri')
    track_name = request.form.get('track_name')
    artist_name = request.form.get('artist_name')
    is_admin = request.form.get('is_admin') == 'true'

    logger.debug(f"Attempting to queue: {track_name} by {artist_name}")

    if not track_uri:
        logger.error("No track_uri provided in request")
        return jsonify({"status": "error", "type": "error", "message": "No track URI provided"}), 400

    cooldown_period = 1200  # 20 minutes in seconds

    with queue_lock:
        # Get the current session if it exists
        current_session_id = session.get('current_session_id')
        logger.debug(f"Current session ID: {current_session_id}")
        
        if current_session_id:
            current_session = get_session(current_session_id)
            if not current_session:
                logger.error(f"No session found for ID: {current_session_id}")
                return jsonify({"status": "error", "type": "error", "message": "Session not found"}), 404
        else:
            # If no session, we'll just add to the Spotify queue without session functionality
            logger.info("No active session, proceeding without session functionality")
            current_session = None

        if current_session and not is_admin and current_session.is_track_on_cooldown(track_uri, cooldown_period):
            logger.debug(f"Track on cooldown: {track_name} by {artist_name}")
            return jsonify({"status": "error", "message": "This track was recently played. Please try again later."}), 200

        sp = spotipy.Spotify(auth=token_info['access_token'])
        
        try:
            sp.add_to_queue(track_uri)
            logger.debug(f"Successfully added to Spotify queue: {track_name} by {artist_name}")
            
            # Add track to recent_tracks
            recent_tracks[track_uri] = {
                'name': track_name,
                'artists': artist_name,
                'added_at': time.time()
            }
            
            if current_session:
                # Add track to session
                result = add_track_to_session(current_session, track_uri, track_name, artist_name)
                logger.debug(f"Result of add_track_to_session: {result}")
                
                if result['added_to_playlist']:
                    logger.info(f"Track added to playlist: {track_name} by {artist_name}")
                else:
                    logger.warning(f"Track not added to playlist: {track_name} by {artist_name}")
                
                return jsonify({"status": "success", "type": "success", "message": "Track added to queue and session!"})
            else:
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
        sp.start_playback(uris=[track_uri])
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

@bp.route('/current_queue')
def current_queue():
    token_info = get_token()
    if not token_info:
        logger.warning("No token info available in current_queue route")
        return jsonify({"error": "Not authenticated"}), 401

    try:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        queue_info = sp._get('me/player/queue')
        current_track = sp.currently_playing()

        user_queue = []
        radio_queue = []

        if queue_info and 'queue' in queue_info:
            for track in queue_info['queue']:
                track_info = {
                    'name': track['name'],
                    'artists': ', '.join([artist['name'] for artist in track['artists']]),
                    'uri': track['uri']
                }
                # Check if the track is in recent_tracks
                if track['uri'] in recent_tracks:
                    user_queue.append(track_info)
                else:
                    radio_queue.append(track_info)

        debug_data = {
            'current_track': current_track,
            'user_queue': user_queue,
            'radio_queue': radio_queue
        }
        formatted_output = format_debug_output(debug_data)
        logger.debug(f"Queue Information:\n{formatted_output}")

        return jsonify({
            'current_track': {
                'name': current_track['item']['name'],
                'artists': ', '.join([artist['name'] for artist in current_track['item']['artists']])
            } if current_track and current_track.get('item') else None,
            'user_queue': user_queue,
            'radio_queue': radio_queue[:5]  # Limit to first 5 tracks
        })
    except Exception as e:
        logger.error(f"Error fetching queue: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "An unexpected error occurred"}), 500

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

@bp.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@bp.route('/create_session', methods=['POST'])
def route_create_session():
    return create_new_session()

@bp.route('/end_session', methods=['POST'])
def end_session():
    session_id = session.get('current_session_id')
    if session_id:
        try:
            delete_session(session_id)
            session.pop('current_session_id', None)
            session.pop('token_info', None)
            logger.info(f"Session ended: {session_id}")
            return jsonify({"status": "success", "message": "Session ended successfully"})
        except Exception as e:
            logger.error(f"Error ending session {session_id}: {str(e)}")
            return jsonify({"status": "error", "message": f"Failed to end session: {str(e)}"}), 500
    else:
        logger.warning("Attempt to end session when no active session found")
        return jsonify({"status": "error", "message": "No active session found"}), 400