// Service Worker Registration
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/service-worker.js')
        .then(registration => console.log('ServiceWorker registration successful with scope: ', registration.scope))
        .catch(error => console.error('ServiceWorker registration failed: ', error));
}

// Debug mode handling
let debugMode = false;

function setDebugMode(isDebug) {
    debugMode = isDebug;
    if (debugMode) {
        console.log('Debug mode is enabled');
    }
}

function debugLog(...args) {
    if (debugMode) {
        console.log(...args);
    }
}

function initializeDebugMode() {
    fetch('/debug_status')
        .then(response => response.json())
        .then(data => {
            setDebugMode(data.debug_mode);
        })
        .catch(error => console.error('Error fetching debug status:', error));
}

// Notification handling
function showNotification(message, type) {
    debugLog('showNotification called with message:', message, 'and type:', type);
    const notification = document.getElementById('notification');
    if (!notification) {
        console.error('Notification element not found in the DOM');
        return;
    }
    notification.textContent = message;
    notification.className = type === 'error' ? 'error' : '';
    notification.style.display = 'block';
    
    notification.offsetHeight; // Force a reflow
    
    notification.classList.add('show');
    
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => {
            notification.style.display = 'none';
        }, 500);
    }, 3000);
}

// UI helpers
function truncateText(text, maxLength) {
    return text.length > maxLength ? text.substring(0, maxLength - 3) + '...' : text;
}

function updateUIForAdminStatus(isAdmin) {
    debugLog('Updating UI for admin status:', isAdmin);
    const header = document.querySelector('.header-container');
    const playNextButtons = document.querySelectorAll('.play-next-button');
    const iconContainer = document.querySelector('.icon-container');
    
    if (isAdmin) {
        header.classList.add('admin-mode');
        playNextButtons.forEach(button => button.style.display = 'inline-block');
        iconContainer.classList.add('admin-active');
    } else {
        header.classList.remove('admin-mode');
        playNextButtons.forEach(button => button.style.display = 'none');
        iconContainer.classList.remove('admin-active');
    }
}

// Queue management
let userQueue = [];
let radioQueue = [];

function addTrackToQueue(track_uri, trackName, artistName) {
    debugLog(`Attempting to add track to queue: ${trackName} by ${artistName}`);

    const isAdmin = document.querySelector('.header-container').classList.contains('admin-mode');
    
    fetch('/queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ 
            'track_uri': track_uri, 
            'track_name': trackName, 
            'artist_name': artistName,
            'is_admin': isAdmin
        })
    })
    .then(response => response.json())
    .then(data => {
        debugLog('Server response:', data);
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            fetchQueue();
        } else if (data.status === 'cooldown') {
            showNotification(data.message, 'info');
        } else {
            showNotification(data.message, 'error');
        }
    })
    .catch(error => {
        console.error('Error adding track to queue:', error);
        showNotification('Error adding track to queue', 'error');
    });
}

function playTrackNext(track_uri) {
    debugLog('Attempting to play track next:', track_uri);
    fetch('/play_next', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ 'track_uri': track_uri })
    })
    .then(response => response.json())
    .then(data => {
        debugLog('Server response:', data);
        showNotification(data.message, data.type || 'success');
        if (data.status === 'success') {
            fetchQueue();
        }
    })
    .catch(error => {
        console.error('Error playing track next:', error);
        showNotification('Error playing track next', 'error');
    });
}

function fetchQueue() {
    debugLog('Fetching current queue');
    fetch('/current_queue')
        .then(response => response.json())
        .then(data => {
            debugLog('Current track:', data.current_track ? `${data.current_track.name} by ${data.current_track.artists}` : 'None');
            debugLog('User queue:', data.user_queue.map(t => `${t.name} by ${t.artists}`).join(', '));
            debugLog('Radio queue (first 5):', data.radio_queue.slice(0, 5).map(t => `${t.name} by ${t.artists}`).join(', '));
            
            userQueue = data.user_queue;
            radioQueue = data.radio_queue;
            updateQueueDisplay(data.current_track);
        })
        .catch(error => console.error('Error fetching queue:', error));
}

function updateQueueDisplay(currentTrack) {
    debugLog('Updating queue display');
    const queueContainer = document.getElementById('queue');
    queueContainer.innerHTML = '';

    if (currentTrack) {
        queueContainer.innerHTML += `
            <div class="queue-item current-track">
                ${currentTrack.name} by ${currentTrack.artists}
            </div>`;
    }

    if (userQueue.length > 0) {
        queueContainer.innerHTML += '<h3>In Queue</h3>';
        userQueue.forEach(track => {
            queueContainer.innerHTML += `
                <div class="queue-item">
                    ${track.name} by ${track.artists}
                </div>`;
        });
    }

    if (radioQueue.length > 0) {
        queueContainer.innerHTML += '<h3>Up Next</h3>';
        radioQueue.slice(0, 5).forEach(track => {
            queueContainer.innerHTML += `
                <div class="queue-item">
                    ${track.name} by ${track.artists}
                </div>`;
        });

        if (radioQueue.length > 5) {
            queueContainer.innerHTML += `
                <div class="queue-item more-tracks">
                    + ${radioQueue.length - 5} more tracks
                </div>`;
        }
    }

    if (userQueue.length === 0 && radioQueue.length === 0) {
        queueContainer.innerHTML += '<p>No tracks in queue</p>';
    }
}

// Search and recommendations
function performSearch(query) {
    debugLog('Performing search. Query:', query);
    fetch('/check_admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ 'query': query })
    })
    .then(response => response.json())
    .then(data => {
        debugLog('Admin check response:', data);
        if (data.is_admin) {
            updateUIForAdminStatus(true);
            showNotification('Admin mode activated', 'success');
            document.querySelector('.results').innerHTML = '';
        } else {
            fetchRecommendations(query);
        }
    })
    .catch(error => {
        console.error('Error checking admin status:', error);
        fetchRecommendations(query);
    });
}

function fetchRecommendations(query) {
    debugLog('Fetching recommendations for query:', query);
    fetch(`/recommendations?query=${encodeURIComponent(query)}`)
        .then(response => response.json())
        .then(data => {
            debugLog('Recommendations data:', data);
            const resultsContainer = document.querySelector('.results');
            resultsContainer.innerHTML = '';
            if (data.length === 0) {
                resultsContainer.innerHTML = '<p>No results found</p>';
            } else {
                data.forEach(track => {
                    const trackElement = document.createElement('div');
                    trackElement.className = 'result-item';
                    trackElement.innerHTML = `
                        ${track.album_art ? `<img src="${track.album_art}" alt="${track.name} album art" class="album-art">` : ''}
                        <div class="track-info">
                            <p class="track-name" title="${track.name}">${truncateText(track.name, 40)}</p>
                            <p class="track-artist" title="${track.artists}">${truncateText(track.artists, 40)}</p>
                        </div>
                        <div class="button-container">
                            <button class="add-to-queue-button">Add to Queue</button>
                            ${document.querySelector('.header-container').classList.contains('admin-mode') ?
                                `<button class="play-next-button">Play Next</button>` : ''}
                        </div>
                    `;

                    const addToQueueButton = trackElement.querySelector('.add-to-queue-button');
                    addToQueueButton.addEventListener('click', () => addTrackToQueue(track.uri, track.name, track.artists));

                    const playNextButton = trackElement.querySelector('.play-next-button');
                    if (playNextButton) {
                        playNextButton.addEventListener('click', () => playTrackNext(track.uri));
                    }

                    resultsContainer.appendChild(trackElement);
                });
            }
        })
        .catch(error => console.error('Error fetching recommendations:', error));
}

// Admin functions

function checkAdminKeyword(query) {
    debugLog('Checking for admin keyword');
    return fetch('/check_admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ 'query': query })
    })
    .then(response => response.json())
    .then(data => {
        debugLog('Admin check response:', data);
        updateUIForAdminStatus(data.is_admin);
        return data.is_admin;
    })
    .catch(error => {
        console.error('Error checking admin keyword:', error);
        return false;
    });
}

function checkAdminStatus() {
    debugLog('Checking admin status');
    fetch('/check_admin_status')
        .then(response => response.json())
        .then(data => {
            debugLog('Admin status:', data);
            updateUIForAdminStatus(data.is_admin);
        })
        .catch(error => {
            console.error('Error checking admin status:', error);
        });
}

function checkAndSyncAdminStatus() {
    fetch('/check_admin_status')
        .then(response => response.json())
        .then(data => {
            debugLog('Admin status check:', data);
            updateUIForAdminStatus(data.is_admin);
        })
        .catch(error => {
            console.error('Error checking admin status:', error);
        });
}

function deactivateAdminMode() {
    debugLog('Attempting to deactivate admin mode');
    fetch('/deactivate_admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(response => response.json())
    .then(data => {
        debugLog('Admin deactivation response:', data);
        if (data.status === 'success') {
            updateUIForAdminStatus(false);
            showNotification('Admin mode deactivated', 'info');
        } else if (data.status === 'error' && data.message === 'Not in admin mode') {
            console.error('Backend reports not in admin mode, but frontend thinks it is. Syncing state.');
            updateUIForAdminStatus(false);
            showNotification('Admin mode deactivated', 'info');
        } else {
            throw new Error(data.message || 'Failed to deactivate admin mode');
        }
    })
    .catch(error => {
        console.error('Error deactivating admin mode:', error);
        showNotification('Error deactivating admin mode: ' + error.message, 'error');
    });
}

// Event Listeners and Initialization
document.addEventListener('DOMContentLoaded', () => {
    debugLog('DOM fully loaded and parsed');
    
    const searchInput = document.querySelector('input[name="query"]');
    const searchButton = document.querySelector('button[type="submit"]');
    const iconContainer = document.querySelector('.icon-container');
    const tipModal = document.getElementById('tipModal');
    const tipButton = document.getElementById('tipButton');

    initializeDebugMode();
    fetchQueue();
    setInterval(fetchQueue, 5000);
    checkAdminStatus();

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value;
        if (query.length > 0) {
            performSearch(query);
        } else {
            document.querySelector('.results').innerHTML = '';
        }
    });

    searchButton.addEventListener('click', (e) => {
        e.preventDefault();
        const query = searchInput.value;
        if (query.length > 0) {
            performSearch(query);
        }
    });

    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const query = searchInput.value;
            if (query.length > 0) {
                performSearch(query);
            }
        }
    });

    searchButton.addEventListener('click', (e) => {
        e.preventDefault();
        const query = searchInput.value;
        if (query.length > 0) {
            performSearch(query);
        }
    });

    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const query = searchInput.value;
            if (query.length > 0) {
                performSearch(query);
            }
        }
    });

    searchButton.addEventListener('click', (e) => {
        e.preventDefault();
        const query = searchInput.value;
        if (query.length > 0) {
            performSearch(query);
        }
    });
    

    iconContainer.addEventListener('click', () => {
        if (document.querySelector('.header-container').classList.contains('admin-mode')) {
            deactivateAdminMode();
        } else {
            checkAndSyncAdminStatus();
        }
    });

    // Tip modal functionality
    if (tipButton && typeof qrCodeAvailable !== 'undefined' && qrCodeAvailable) {
        tipButton.style.display = 'inline-block';
        const closeButton = tipModal.querySelector('.close');

        tipButton.onclick = () => {
            tipModal.style.display = 'block';
            setTimeout(() => tipModal.classList.add('show'), 10);
        };

        closeButton.onclick = () => {
            tipModal.classList.remove('show');
            setTimeout(() => tipModal.style.display = 'none', 300);
        };

        window.onclick = (event) => {
            if (event.target == tipModal) {
                tipModal.classList.remove('show');
                setTimeout(() => tipModal.style.display = 'none', 300);
            }
        };
    } else if (tipButton) {
        tipButton.style.display = 'none';
    }
});