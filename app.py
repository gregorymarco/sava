from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid
import json
from datetime import datetime
import os

app = Flask(__name__)

# Production configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24))

# CORS configuration - handle development and production
allowed_origins_env = os.environ.get('ALLOWED_ORIGINS', '*')
if allowed_origins_env == '*':
    # Allow all origins for development/testing
    socketio_cors_origins = '*'  # Allow all for SocketIO
else:
    # Use specific origins for production
    socketio_cors_origins = allowed_origins_env.split(',')

# Use Flask-CORS with specific configuration that works
CORS(app, origins=['*'], supports_credentials=True)

# SocketIO configuration for production
# Use threading mode for maximum compatibility across platforms
async_mode = 'threading'

socketio_kwargs = {
    'cors_allowed_origins': socketio_cors_origins,
    'async_mode': async_mode,
    'logger': False,
    'engineio_logger': False
}

# Enable logging in development
if os.environ.get('FLASK_ENV') == 'development':
    socketio_kwargs['logger'] = True
    socketio_kwargs['engineio_logger'] = True

socketio = SocketIO(app, **socketio_kwargs)

# In-memory storage for lobbies and game states
lobbies = {}
game_states = {}

# Load game configuration from JSON file
def load_game_config():
    """Load game configuration from JSON file."""
    try:
        config_path = os.path.join(app.static_folder, 'game-config.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
        print("Game configuration loaded successfully")
        return config
    except Exception as e:
        print(f"Error loading game configuration: {e}")
        # Fallback to default configuration
        return get_default_game_config()

def get_default_game_config():
    """Fallback default game configuration if JSON loading fails."""
    return {}

# Load configuration at startup
GAME_CONFIG = load_game_config()

# Game configuration constants
MAX_PLAYERS = GAME_CONFIG["game_rules"]["max_players"]
AUTO_START_THRESHOLD = GAME_CONFIG["game_rules"]["auto_start_threshold"]
SPIDER_DICE_MIN_TURN = GAME_CONFIG["game_rules"]["spider_dice_min_turn"]
TURN_TIME_LIMIT = GAME_CONFIG["game_rules"]["turn_time_limit_seconds"]
RESURRECTION_ZONES = GAME_CONFIG["resurrection_zones"]

# Board connectivity - defines which nodes are connected
# This is now loaded from the shared game-config.json file
BOARD_CONNECTIONS = GAME_CONFIG["board_connections"]

def get_strand_nodes():
    """Get list of strand node arrays for backward compatibility."""
    return [strand_def['nodes'] for strand_def in GAME_CONFIG['strand_definitions']]

def get_neighboring_nodes(node_id):
    """Get all neighboring nodes for a given node."""
    neighbors = set()
    
    # Add ring neighbors (adjacent nodes in the same ring)
    if node_id.startswith('R'):
        parts = node_id.split('N')
        ring_num = parts[0]
        node_num = int(parts[1])
        ring_size = 16
        
        # Adjacent nodes in the ring
        neighbors.add(f'{ring_num}N{(node_num - 1) % ring_size}')
        neighbors.add(f'{ring_num}N{(node_num + 1) % ring_size}')
    
    # Add center neighbors (if it's a center node)
    if node_id.startswith('C'):
        center_index = int(node_id[1])
        # Center nodes form a diamond pattern based on strand connections:
        # C0 connects to C1 and C2
        # C1 connects to C0 and C3  
        # C2 connects to C0 and C3
        # C3 connects to C1 and C2
        if center_index == 0:  # C0
            neighbors.add('C1')  # Horizontal strand 2
            neighbors.add('C2')  # Vertical strand 1
        elif center_index == 1:  # C1
            neighbors.add('C0')  # Horizontal strand 2
            neighbors.add('C3')  # Vertical strand 2
        elif center_index == 2:  # C2
            neighbors.add('C0')  # Vertical strand 1
            neighbors.add('C3')  # Horizontal strand 1
        elif center_index == 3:  # C3
            neighbors.add('C1')  # Vertical strand 2
            neighbors.add('C2')  # Horizontal strand 1
    
    # Add strand neighbors
    for strand_def in GAME_CONFIG['strand_definitions']:
        strand = strand_def['nodes']
        if node_id in strand:
            idx = strand.index(node_id)
            if idx > 0:
                neighbors.add(strand[idx - 1])
            if idx < len(strand) - 1:
                neighbors.add(strand[idx + 1])
    
    return neighbors

def is_enemy_piece(piece_name, current_color):
    """Check if a piece belongs to the enemy."""
    if not piece_name:
        return False
    return piece_name.startswith('blue_' if current_color == 'red' else 'red_')

def has_enemy_neighbors(node_id, board_state, current_color):
    """Check if a node has enemy pieces as neighbors."""
    neighbors = get_neighboring_nodes(node_id)
    for neighbor_id in neighbors:
        if neighbor_id in board_state:
            piece_name = board_state[neighbor_id]
            if is_enemy_piece(piece_name, current_color):
                return True
    return False

def would_move_put_matron_in_check(from_node, to_node, board_state, current_color):
    """Check if a move would put the Matron Mother in check by calculating enemy legal moves."""
    # Create a temporary board state to simulate the move
    temp_board = board_state.copy()
    
    # Simulate the move
    if from_node in temp_board:
        del temp_board[from_node]
    temp_board[to_node] = f"{current_color}_matron mother"
    
    # Find the Matron Mother's position after the move
    matron_mother_node = to_node
    
    # Check if any enemy piece can capture the Matron Mother at the new position
    enemy_color = 'blue' if current_color == 'red' else 'red'
    for node, piece in temp_board.items():
        if piece.startswith(enemy_color + '_'):
            # Use existing get_legal_moves functions to avoid code duplication
            if 'orc' in piece:
                legal_moves = get_legal_moves_for_orc(node, temp_board, enemy_color)
            elif 'priestess' in piece:
                legal_moves = get_legal_moves_for_priestess(node, temp_board, enemy_color)
            elif 'weaponmaster' in piece:
                legal_moves = get_legal_moves_for_weaponmaster(node, temp_board, enemy_color)
            elif 'wizard' in piece:
                legal_moves = get_legal_moves_for_wizard(node, temp_board, enemy_color)
            else:
                # For other pieces, use neighboring nodes
                legal_moves = get_neighboring_nodes(node)
            
            # Check if any of these moves would capture the Matron Mother
            if matron_mother_node in legal_moves:
                return True  # Move would put Matron Mother in check
    
    return False  # Move is safe

def get_legal_moves_for_orc(node_id, board_state, current_color, spider_control=False):
    """Calculate legal moves for an Orc piece."""
    legal_moves = set()
    neighbors = get_neighboring_nodes(node_id)
    
    for neighbor_id in neighbors:
        # Check if this is a capture move
        if neighbor_id in board_state:
            piece_name = board_state[neighbor_id]
            if spider_control:
                # In spider control mode, can capture any piece
                legal_moves.add(neighbor_id)
            elif not piece_name.startswith(current_color + '_'):
                # Regular mode: can only capture enemy pieces
                legal_moves.add(neighbor_id)
            # Skip if it's own piece and not in spider control mode
        else:
            # This is a regular move - check if we're moving away from enemies
            current_has_enemies = has_enemy_neighbors(node_id, board_state, current_color)
            new_has_enemies = has_enemy_neighbors(neighbor_id, board_state, current_color)
            
            # Move is legal if:
            # 1. We're not moving away from enemies, OR
            # 2. We're moving away but there are still enemies at the new position
            if not current_has_enemies or new_has_enemies:
                legal_moves.add(neighbor_id)
    return list(legal_moves)

def get_legal_moves_for_priestess(node_id, board_state, current_color, spider_control=False):
    """Calculate legal moves for a Priestess piece."""
    legal_moves = set()
    
    # Get all possible paths (rings and strands) that this node is part of
    paths = []
    
    # Add ring path if it's a ring node
    if node_id.startswith('R'):
        parts = node_id.split('N')
        ring_num = parts[0]
        ring_size = 16
        ring_path = [f'{ring_num}N{i}' for i in range(ring_size)]
        paths.append(ring_path)
    
    # Add strand paths if this node is part of any strands
    for strand_def in GAME_CONFIG['strand_definitions']:
        strand = strand_def['nodes']
        if node_id in strand:
            paths.append(strand)
    
    # For each path, calculate legal moves along that path
    for path in paths:
        current_idx = path.index(node_id)
        path_length = len(path)
        
        # For rings, we need to handle the circular nature
        is_ring = path[0].startswith('R')
        
        # Check moves in the positive direction
        for i in range(1, path_length):
            target_idx = (current_idx + i) % path_length
            target_node = path[target_idx]
            
            # If there's a piece at this node, we can't move past it
            if target_node in board_state:
                # Check if we can capture
                piece_name = board_state[target_node]
                if spider_control or is_enemy_piece(piece_name, current_color):
                    legal_moves.add(target_node)
                # Stop checking this direction (can't move through pieces)
                break
            else:
                # Empty node, can move here
                legal_moves.add(target_node)
        
        # Check moves in the negative direction
        for i in range(1, path_length):
            target_idx = (current_idx - i) % path_length
            target_node = path[target_idx]
            
            # If there's a piece at this node, we can't move past it
            if target_node in board_state:
                # Check if we can capture
                piece_name = board_state[target_node]
                if spider_control or is_enemy_piece(piece_name, current_color):
                    legal_moves.add(target_node)
                # Stop checking this direction (can't move through pieces)
                break
            else:
                # Empty node, can move here
                legal_moves.add(target_node)
    
    return list(legal_moves)

def get_legal_moves_for_matron_mother(node_id, board_state, current_color, spider_control=False):
    """Calculate legal moves for a Matron Mother piece."""
    legal_moves = set()
    
    # Get all neighboring nodes
    neighbors = get_neighboring_nodes(node_id)
    
    for neighbor_id in neighbors:
        # Check if this is a capture move
        if neighbor_id in board_state:
            piece_name = board_state[neighbor_id]
            if spider_control:
                # In spider control mode, can capture any piece (but still check for safety)
                if not would_move_put_matron_in_check(node_id, neighbor_id, board_state, current_color):
                    legal_moves.add(neighbor_id)
            elif not piece_name.startswith(current_color + '_'):
                # Regular mode: can only capture enemy pieces
                if not would_move_put_matron_in_check(node_id, neighbor_id, board_state, current_color):
                    legal_moves.add(neighbor_id)
            # Skip if it's own piece and not in spider control mode
        else:
            # Empty node - check if this move would put the Matron Mother in check
            if not would_move_put_matron_in_check(node_id, neighbor_id, board_state, current_color):
                legal_moves.add(neighbor_id)
    
    return list(legal_moves)

def get_legal_moves_for_weaponmaster(node_id, board_state, current_color, spider_control=False):
    """Calculate legal moves for a Weaponmaster piece."""
    legal_moves = set()
    
    # Get all neighboring nodes for the first move
    first_neighbors = get_neighboring_nodes(node_id)
    
    for first_neighbor_id in first_neighbors:
        # Check if first neighbor is blocked
        if first_neighbor_id in board_state:
            piece_name = board_state[first_neighbor_id]
            if not spider_control and piece_name.startswith(current_color + '_'):
                continue  # Can't move through friendly pieces in normal mode
        
        # Get neighbors of the first neighbor for the second move
        second_neighbors = get_neighboring_nodes(first_neighbor_id)
        for second_neighbor_id in second_neighbors:
            # Skip if it's the original starting position (can't return to start)
            if second_neighbor_id == node_id:
                continue
            
            # Check if second position is blocked
            if second_neighbor_id in board_state:
                piece_name = board_state[second_neighbor_id]
                if not spider_control and piece_name.startswith(current_color + '_'):
                    continue  # Can't end on friendly pieces in normal mode
            
            # Add the complete two-node path as a legal move
            # Format: "first_node->second_node" to represent the complete move
            move_path = f"{first_neighbor_id}->{second_neighbor_id}"
            legal_moves.add(move_path)
    
    return list(legal_moves)

def get_legal_moves_for_wizard(node_id, board_state, current_color, spider_control=False):
    """Calculate legal moves for a Wizard piece."""
    legal_moves = set()
    
    # Get all neighboring nodes for the first move
    first_neighbors = get_neighboring_nodes(node_id)
    
    for first_neighbor_id in first_neighbors:
        # Skip if first neighbor is the starting position
        if first_neighbor_id == node_id:
            continue
            
        # Wizard can move through any pieces (friendly or enemy) - no restrictions on intermediate moves
        
        # Get neighbors of the first neighbor for the second move
        second_neighbors = get_neighboring_nodes(first_neighbor_id)
        for second_neighbor_id in second_neighbors:
            # Skip if it's the original starting position or duplicates the first node
            if second_neighbor_id == node_id or second_neighbor_id == first_neighbor_id:
                continue
            
            # Wizard can move through any pieces on second move too
            
            # Get neighbors of the second neighbor for the third move
            third_neighbors = get_neighboring_nodes(second_neighbor_id)
            for third_neighbor_id in third_neighbors:
                # Skip if it's the original starting position or duplicates any previous node
                if (third_neighbor_id == node_id or 
                    third_neighbor_id == first_neighbor_id or 
                    third_neighbor_id == second_neighbor_id):
                    continue
                
                # Check final destination
                if third_neighbor_id in board_state:
                    piece_name = board_state[third_neighbor_id]
                    if not spider_control and piece_name.startswith(current_color + '_'):
                        continue  # Can't end on friendly pieces in normal mode
                
                # Add the complete three-node path as a legal move
                # Format: "first_node->second_node->third_node" to represent the complete move
                move_path = f"{first_neighbor_id}->{second_neighbor_id}->{third_neighbor_id}"
                legal_moves.add(move_path)
    
    return list(legal_moves)

def get_legal_moves(piece_name, node_id, board_state, current_color, spider_control=False):
    """Get legal moves for any piece type."""
    if 'orc' in piece_name:
        return get_legal_moves_for_orc(node_id, board_state, current_color, spider_control)
    elif 'priestess' in piece_name:
        return get_legal_moves_for_priestess(node_id, board_state, current_color, spider_control)
    elif 'matron mother' in piece_name:
        return get_legal_moves_for_matron_mother(node_id, board_state, current_color, spider_control)
    elif 'weaponmaster' in piece_name:
        return get_legal_moves_for_weaponmaster(node_id, board_state, current_color, spider_control)
    elif 'wizard' in piece_name:
        return get_legal_moves_for_wizard(node_id, board_state, current_color, spider_control)
    else:
        # For other pieces, return all neighboring nodes (placeholder)
        neighbors = get_neighboring_nodes(node_id)
        legal_moves = []
        for neighbor_id in neighbors:
            if neighbor_id not in board_state or is_enemy_piece(board_state[neighbor_id], current_color):
                legal_moves.append(neighbor_id)
            elif spider_control and neighbor_id in board_state:
                # In spider control mode, can capture any piece
                legal_moves.append(neighbor_id)
        return legal_moves

def can_orc_promote(piece_name, destination_node, player_color):
    """Check if an orc can be promoted at the destination node."""
    if 'orc' not in piece_name:
        return False
    
    # Check if destination is in resurrection zone for this player
    resurrection_nodes = RESURRECTION_ZONES.get(player_color, [])
    return destination_node in resurrection_nodes

def get_promotable_pieces(captured_pieces):
    """Get list of non-orc pieces that can be used for promotion."""
    promotable = []
    for piece in captured_pieces:
        if 'orc' not in piece and 'matron mother' not in piece:
            promotable.append(piece)
    return promotable

def notify_lobby_update(lobby_id, event_type, data=None):
    """Send WebSocket notification to all players in a lobby."""
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        notification = {
            'event_type': event_type,
            'lobby_info': lobby.get_lobby_info(),
            'data': data
        }
        # Convert datetime objects to strings for JSON serialization
        notification = json.loads(json.dumps(notification, default=str))
        socketio.emit('lobby_update', notification, room=lobby_id)

class Lobby:
    def __init__(self, lobby_id):
        self.lobby_id = lobby_id
        self.players = []
        self.spectators = []
        self.created_at = datetime.now()
        # Turn timer configuration (in seconds)
        self.turn_time_limit = TURN_TIME_LIMIT
        
        self.game_state = {
            'board': {},
            'current_turn': 'red',
            'game_started': False,
            'last_move': None,
            'game_pieces': {},  # Will store piece positions
        'captured_pieces': {
            'red': [],  # Pieces captured by red player
            'blue': []  # Pieces captured by blue player
        },
        'player_turn_numbers': {
            'red': 0,  # Red player's turn counter
            'blue': 0   # Blue player's turn counter
        },
        'player_time_remaining': {
            'red': self.turn_time_limit,  # Time remaining for red player (seconds)
            'blue': self.turn_time_limit   # Time remaining for blue player (seconds)
        },
        'turn_start_time': None,  # Timestamp when current turn started
        'chat_messages': [],  # Chat messages for this lobby
        'promotion_mode': False,  # True when waiting for piece selection
        'promotion_player': None,  # Player who can promote
        'promotion_node': None,  # Node where promotion is happening
        'promotion_orc': None  # Name of the orc being promoted
        }

    def add_player(self, player_id, player_name):
        if len(self.players) < 2:
            # Check which color slots are available
            red_slot_occupied = any(p['color'] == 'red' for p in self.players)
            blue_slot_occupied = any(p['color'] == 'blue' for p in self.players)
            
            # Assign player to the first available color slot
            if not red_slot_occupied:
                assigned_color = 'red'
            elif not blue_slot_occupied:
                assigned_color = 'blue'
            else:
                # Both slots are occupied (shouldn't happen with len < 2 check, but just in case)
                assigned_color = 'red' if len(self.players) == 0 else 'blue'
            
            current_players_str = ', '.join([f"{p['id']}:{p['color']}" for p in self.players])
           
            self.players.append({
                'id': player_id,
                'name': player_name,
                'color': assigned_color,
                'joined_at': datetime.now()
            })
            
            return 'player'
        else:
            self.spectators.append({
                'id': player_id,
                'name': player_name,
                'joined_at': datetime.now()
            })
            return 'spectator'

    def remove_player(self, player_id):
        # Remove from players
        self.players = [p for p in self.players if p['id'] != player_id]
        # Remove from spectators
        self.spectators = [s for s in self.spectators if s['id'] != player_id]
        
        # If no players left, mark lobby for cleanup
        if len(self.players) == 0 and len(self.spectators) == 0:
            return True
        return False

    def get_lobby_info(self):
        return {
            'lobby_id': self.lobby_id,
            'players': self.players,
            'spectators': self.spectators,
            'game_state': self.game_state,
            'can_start': len(self.players) == 2
        }

    def _update_turn_timer(self, player_color):
        """Update the turn timer for the current player."""
        if not self.game_state['game_started'] or not self.game_state['turn_start_time']:
            return
        
        # Calculate elapsed time since turn started
        current_time = datetime.now().timestamp()
        elapsed_time = current_time - self.game_state['turn_start_time']
        
        # Subtract elapsed time from player's remaining time
        self.game_state['player_time_remaining'][player_color] -= elapsed_time
        
        # Ensure time doesn't go below 0
        if self.game_state['player_time_remaining'][player_color] < 0:
            self.game_state['player_time_remaining'][player_color] = 0
        
    def _start_next_player_timer(self, next_player_color):
        """Start the timer for the next player's turn."""
        self.game_state['turn_start_time'] = datetime.now().timestamp()

    def _check_time_expired(self, player_color):
        """Check if a player's time has expired."""
        # First check if time remaining is already 0
        if self.game_state['player_time_remaining'][player_color] <= 0:
            return True
        
        # If not, check if elapsed time this turn would exhaust remaining time
        if not self.game_state.get('turn_start_time'):
            return False
        
        current_time = datetime.now().timestamp()
        elapsed_this_turn = current_time - self.game_state['turn_start_time']
        remaining_time = self.game_state['player_time_remaining'][player_color]
        
        # Player has timed out if elapsed time >= remaining time
        has_timed_out = elapsed_this_turn >= remaining_time

        return has_timed_out

    def handle_player_timeout(self, player_color):
        """Handle when a player runs out of time."""
        if not self.game_state['game_started'] or self.game_state.get('game_over'):
            return False, "Game not active"
        
        # Verify the player has actually timed out
        if not self._check_time_expired(player_color):
            return False, "Player has not timed out"
        
        # Game over - the other player wins
        winner_color = 'blue' if player_color == 'red' else 'red'
        self.game_state['game_over'] = True
        self.game_state['winner'] = winner_color
        self.game_state['game_end_reason'] = 'timeout'
        self.game_state['timeout_player'] = player_color
        
        # Notify all players about the timeout
        notify_lobby_update(self.lobby_id, 'player_timeout', {
            'timeout_player': player_color,
            'winner': winner_color,
            'game_end_reason': 'timeout'
        })
        
        # Send game over event
        notify_lobby_update(self.lobby_id, 'game_over', {
            'winner': winner_color,
            'game_end_reason': 'timeout',
            'timeout_player': player_color,
            'final_board': self.game_state['board']
        })
        
        return True, self.game_state

    def setup_game_board(self, piece_mapping):
        """
        Set up the initial game board with pieces.
        
        Args:
            piece_mapping (dict): Dictionary mapping piece names to node positions
                Format: {
                    'red_matron mother': 'C0',
                    'red_wizard': 'C1',
                    'black_matron mother': 'R3N0',
                    ...
                }
        """
        self.game_state['game_pieces'] = piece_mapping
        self.game_state['game_started'] = True
        self.game_state['current_turn'] = 'red'
        self.game_state['last_move'] = None
        self.game_state['player_turn_numbers'] = {'red': 0, 'blue': 0}  # Initialize player turn counters
        
        # Start the turn timer for the first player (red)
        self.game_state['turn_start_time'] = datetime.now().timestamp()
        
        # Update the board state to reflect piece positions
        self.game_state['board'] = {}
        for piece_name, node_id in piece_mapping.items():
            self.game_state['board'][node_id] = piece_name
        
        # Notify all players about the game start
        notify_lobby_update(self.lobby_id, 'game_started', self.game_state)

    def auto_start_game(self):
        """Automatically start the game with default piece mapping when two players join."""

        # Use shared configuration for piece placement
        # Note: This would need to be loaded from the shared config file
        # For now, keeping the existing mapping but it should be moved to game-config.js
        piece_mapping = GAME_CONFIG["initial_piece_placement"]
        
        self.setup_game_board(piece_mapping)

    def get_legal_moves_for_piece(self, node_id):
        """Get legal moves for a piece at the given node."""
        if not self.game_state['game_started']:
            return []
        
        if node_id not in self.game_state['board']:
            return []
        
        piece_name = self.game_state['board'][node_id]
        current_color = piece_name.split('_')[0]
        
        # Check if this is a controlled piece
        is_controlled = self.game_state.get('controlled_piece_node') == node_id
        
        # Check if it's the current player's turn OR if this is a controlled enemy piece
        if not is_controlled and current_color != self.game_state['current_turn']:
            return []
        
        # Get basic legal moves (now that we know this piece can be moved)
        basic_legal_moves = get_legal_moves(piece_name, node_id, self.game_state['board'], current_color, spider_control=is_controlled)

        # For controlled pieces, use the controlling player's color for check logic
        controlling_color = self.game_state['current_turn'] if is_controlled else current_color
        
        # If the controlling player is in check, filter moves to only include those that resolve the check
        if self._is_player_in_check(controlling_color):
            resolving_moves = []
            for move in basic_legal_moves:
                if self._does_move_resolve_check(node_id, move, piece_name, controlling_color):
                    resolving_moves.append(move)
            return resolving_moves
        
        # If not in check, filter out moves that would put the controlling player in check
        safe_moves = []
        for move in basic_legal_moves:
            if self._is_move_safe_for_matron_mother(node_id, move, piece_name, controlling_color):
                safe_moves.append(move)
        return safe_moves

    def execute_move(self, from_node, to_node, player_id):
        """Execute a move and notify all players."""
        # Get the piece being moved
        piece_name = self.game_state['board'].get(from_node)
        if not piece_name:
            return False, "No piece at source node"
        
        # Verify it's the player's piece
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player or not piece_name.startswith(player['color'] + '_'):
            return False, "Not your piece"
        
        # Verify the move is legal
        legal_moves = self.get_legal_moves_for_piece(from_node)
        if to_node not in legal_moves:
            return False, "Illegal move"
        
        # Check if the player is currently in check
        if self._is_player_in_check(player['color']):
            # If in check, the move must resolve the check
            if not self._does_move_resolve_check(from_node, to_node, piece_name, player['color']):
                return False, "You are in check! You must make a move that resolves the check"
        else:
            # If not in check, check if this move would put the player's own Matron Mother in check
            if not self._is_move_safe_for_matron_mother(from_node, to_node, piece_name, player['color']):
                return False, "This move would put your Matron Mother in check"
        
        # Handle weaponmaster special movement (two-node path with potential captures)
        captured_pieces = []
        if 'weaponmaster' in piece_name and '->' in to_node:
            # Parse the two-node path
            nodes = to_node.split('->')
            if len(nodes) == 2:
                first_node, second_node = nodes
                
                # Check for captures on first node
                if first_node in self.game_state['board']:
                    captured_piece = self.game_state['board'][first_node]
                    captured_pieces.append(captured_piece)
                    self.game_state['captured_pieces'][player['color']].append(captured_piece)
                    # Remove the captured piece from the board
                    del self.game_state['board'][first_node]
                
                # Check for captures on second node
                if second_node in self.game_state['board']:
                    captured_piece = self.game_state['board'][second_node]
                    captured_pieces.append(captured_piece)
                    self.game_state['captured_pieces'][player['color']].append(captured_piece)
                    # Remove the captured piece from the board
                    del self.game_state['board'][second_node]
                
                # Remove piece from source and place at final destination
                del self.game_state['board'][from_node]
                self.game_state['board'][second_node] = piece_name
                
                # Update game state
                self.game_state['last_move'] = {
                    'from': from_node,
                    'to': second_node,
                    'intermediate_node': first_node,
                    'piece': piece_name,
                    'captured': captured_pieces,
                    'player': player['color'],
                    'move_type': 'weaponmaster_two_node'
                }
            else:
                return False, "Invalid weaponmaster move format"
        elif 'wizard' in piece_name and '->' in to_node:
            # Parse the three-node path
            nodes = to_node.split('->')
            if len(nodes) == 3:
                first_node, second_node, third_node = nodes
                
                # Check if final destination is occupied by a friendly piece
                if third_node in self.game_state['board']:
                    final_piece = self.game_state['board'][third_node]
                    if final_piece.startswith(player['color'] + '_'):
                        return False, "Cannot end move on a friendly piece"
                
                # Check for captures only on final destination (third node)
                if third_node in self.game_state['board']:
                    captured_piece = self.game_state['board'][third_node]
                    captured_pieces.append(captured_piece)
                    self.game_state['captured_pieces'][player['color']].append(captured_piece)
                    # Remove the captured piece from the board
                    del self.game_state['board'][third_node]
                
                # Remove piece from source and place at final destination
                del self.game_state['board'][from_node]
                self.game_state['board'][third_node] = piece_name
                
                # Update game state
                self.game_state['last_move'] = {
                    'from': from_node,
                    'to': third_node,
                    'intermediate_nodes': [first_node, second_node],
                    'piece': piece_name,
                    'captured': captured_pieces,
                    'player': player['color'],
                    'move_type': 'wizard_three_node'
                }
            else:
                return False, "Invalid wizard move format"
        else:
            # Regular single-node move
            # Remove piece from source
            del self.game_state['board'][from_node]
            
            # Check if this is a capture
            captured_piece = None
            if to_node in self.game_state['board']:
                captured_piece = self.game_state['board'][to_node]
                # Add to captured pieces list
                self.game_state['captured_pieces'][player['color']].append(captured_piece)
                # Remove the captured piece from the board
                del self.game_state['board'][to_node]
                captured_pieces = [captured_piece]
            
            # Place piece at destination
            self.game_state['board'][to_node] = piece_name
            
            # Update game state
            self.game_state['last_move'] = {
                'from': from_node,
                'to': to_node,
                'piece': piece_name,
                'captured': captured_pieces,
                'player': player['color'],
                'move_type': 'single_node'
            }
        
        # Check for orc promotion before switching turns
        final_destination = to_node
        if '->' in to_node:
            # Handle complex moves to get final destination
            nodes = to_node.split('->')
            if 'weaponmaster' in piece_name and len(nodes) == 2:
                final_destination = nodes[1]
            elif 'wizard' in piece_name and len(nodes) == 3:
                final_destination = nodes[2]
        
        # Check if this move should trigger orc promotion
        if can_orc_promote(piece_name, final_destination, player['color']):
            # Get pieces that were captured by the enemy (our own pieces that can be resurrected)
            enemy_color = 'blue' if player['color'] == 'red' else 'red'
            promotable_pieces = get_promotable_pieces(self.game_state['captured_pieces'][enemy_color])
            if promotable_pieces:
                # Enter promotion mode - don't switch turns yet
                self.game_state['promotion_mode'] = True
                self.game_state['promotion_player'] = player['color']
                self.game_state['promotion_node'] = final_destination
                self.game_state['promotion_orc'] = piece_name
                
                # Notify about promotion opportunity
                notify_lobby_update(self.lobby_id, 'orc_promotion_available', {
                    'promotable_pieces': promotable_pieces,
                    'promotion_node': final_destination,
                    'player': player['color']
                })
                return True, self.game_state
        
        # Update timer for the current player before switching turns
        self._update_turn_timer(player['color'])
        
        # Increment the current player's turn counter
        self.game_state['player_turn_numbers'][player['color']] += 1
        
        # Switch turns
        next_player_color = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
        self.game_state['current_turn'] = next_player_color
        
        # Start timer for the next player
        self._start_next_player_timer(next_player_color)
        
        # Check if the next player is in check
        next_player_color = self.game_state['current_turn']

        # Check if the next player is in checkmate
        if self._is_player_in_checkmate(next_player_color):
            # Game over - the player who just moved wins
            winner_color = player['color']
            self.game_state['game_over'] = True
            self.game_state['winner'] = winner_color
            self.game_state['game_end_reason'] = 'checkmate'
            print(f"🎉 CHECKMATE! {winner_color.upper()} player wins by checkmate!")
            print(f"🏆 Winner: {winner_color}")
            print(f"💀 Loser: {next_player_color}")
            print(f"🎮 Game ended due to checkmate")
            print(f"📊 Final board state: {self.game_state['board']}")
        elif not self._does_player_have_legal_moves(next_player_color):
            # Stalemate - no legal moves but not in check (player who can't move loses)
            winner_color = player['color']  # The player who just moved wins
            self.game_state['game_over'] = True
            self.game_state['winner'] = winner_color
            self.game_state['game_end_reason'] = 'stalemate'
            print(f"🎯 STALEMATE! {next_player_color} player has no legal moves but is not in check")
            print(f"🏆 Winner: {winner_color} (opponent has no legal moves)")
            print(f"💀 Loser: {next_player_color} (no legal moves available)")
            print(f"🎮 Game ended due to stalemate - {winner_color} wins!")
            print(f"📊 Final board state: {self.game_state['board']}")
        
        # Notify all players about the move
        notify_lobby_update(self.lobby_id, 'piece_moved', self.game_state)
        
        # If game is over, send a separate game_over event
        if self.game_state.get('game_over'):
            notify_lobby_update(self.lobby_id, 'game_over', {
                'winner': self.game_state.get('winner'),
                'game_end_reason': self.game_state.get('game_end_reason'),
                'final_board': self.game_state['board']
            })
        
        return True, self.game_state
    
    def roll_spider_dice(self, player_id):
        """Roll spider dice for the current player."""
        # Verify the game has started
        if not self.game_state['game_started']:
            return False, "Game not started"
        
        # Find the player
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player:
            return False, "Player not found"
        
        # Check if it's the current player's turn
        if player['color'] != self.game_state['current_turn']:
            return False, "Not your turn"
        
        # Check turn number requirement
        player_turn_count = self.game_state['player_turn_numbers'][player['color']]
        if player_turn_count < SPIDER_DICE_MIN_TURN:
            return False, f"Must wait until turn {SPIDER_DICE_MIN_TURN} (current turn: {player_turn_count})"
        
        # Roll two d8 dice (1-8)
        import random
        die1 = random.randint(1, 8)
        die2 = random.randint(1, 8)
        
        # Determine spider results (5-8 = spider, 1-4 = knife)
        die1_spider = die1 >= 5
        die2_spider = die2 >= 5
        both_spiders = die1_spider and die2_spider
        both_knives = not die1_spider and not die2_spider
        
        # Reset this player's turn counter to 0 after rolling spider dice (cooldown mechanism)
        self.game_state['player_turn_numbers'][player['color']] = 0
        
        # Create move record
        self.game_state['last_move'] = {
            'move_type': 'spider_dice_roll',
            'player': player['color'],
            'dice_results': {
                'die1': die1,
                'die2': die2,
                'die1_spider': die1_spider,
                'die2_spider': die2_spider,
                'both_spiders': both_spiders,
                'both_knives': both_knives
            }
        }
        
        # If double knives, don't switch turns - player must sacrifice a piece
        if both_knives:
            # Set sacrifice mode for the current player
            self.game_state['sacrifice_mode'] = True
            self.game_state['sacrifice_player'] = player['color']
        elif both_spiders:
            # Set spider control mode for the current player
            self.game_state['spider_control_mode'] = True
            self.game_state['spider_control_player'] = player['color']
        else:
            # Update timer for current player and switch turns for normal results
            self._update_turn_timer(player['color'])
            next_player_color = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
            self.game_state['current_turn'] = next_player_color
            self._start_next_player_timer(next_player_color)

        # Notify all players about the dice roll
        notify_lobby_update(self.lobby_id, 'spider_dice_rolled', self.game_state)
        
        return True, self.game_state
    
    def sacrifice_piece(self, node_id, player_id):
        """Sacrifice a piece (remove it from the board)."""
        # Verify the game has started
        if not self.game_state['game_started']:
            return False, "Game not started"
        
        # Find the player
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player:
            return False, "Player not found"
        
        # Check if player has permission to sacrifice
        sacrifice_mode = self.game_state.get('sacrifice_mode', False)
        sacrifice_player = self.game_state.get('sacrifice_player')
        current_turn = self.game_state['current_turn']
        
        if sacrifice_mode:
            # In sacrifice mode, only the designated sacrifice player can sacrifice
            if player['color'] != sacrifice_player:
                return False, "Only the player who rolled double knives can sacrifice"
        else:
            # Normal mode, check if it's the player's turn
            if player['color'] != current_turn:
                return False, "Not your turn"
        
        # Check if there's a piece at the specified node
        if node_id not in self.game_state['board']:
            return False, "No piece at specified node"
        
        # Check if the piece belongs to the current player
        piece_name = self.game_state['board'][node_id]
        if not piece_name.startswith(player['color'] + '_'):
            return False, "Cannot sacrifice enemy piece"
        
        # Remove the piece from the board
        del self.game_state['board'][node_id]
        
        # Create move record
        self.game_state['last_move'] = {
            'move_type': 'piece_sacrificed',
            'player': player['color'],
            'piece': piece_name,
            'node': node_id
        }
        
        # Clear sacrifice mode and handle turn switching
        if self.game_state.get('sacrifice_mode', False):
            self.game_state['sacrifice_mode'] = False
            self.game_state['sacrifice_player'] = None
            # Update timer and switch turns after sacrifice - player's turn ends immediately
            self._update_turn_timer(player['color'])
            next_player_color = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
            self.game_state['current_turn'] = next_player_color
            self._start_next_player_timer(next_player_color)
        else:
            # Update timer and increment the current player's turn counter for normal sacrifice
            self._update_turn_timer(player['color'])
            self.game_state['player_turn_numbers'][player['color']] += 1
            # Switch turns for normal sacrifice
            next_player_color = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
            self.game_state['current_turn'] = next_player_color
            self._start_next_player_timer(next_player_color)

        # Notify all players about the sacrifice
        notify_lobby_update(self.lobby_id, 'piece_sacrificed', self.game_state)
        
        return True, self.game_state
    
    def control_enemy_piece(self, node_id, player_id):
        """Take control of an enemy piece for one turn."""
        # Verify the game has started
        if not self.game_state['game_started']:
            return False, "Game not started"
        
        # Find the player
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player:
            return False, "Player not found"
        
        # Check if player is in spider control mode
        if not self.game_state.get('spider_control_mode', False):
            return False, "Not in spider control mode"
        
        if self.game_state.get('spider_control_player') != player['color']:
            return False, "Not your spider control turn"
        
        # Check if there's a piece at the specified node
        if node_id not in self.game_state['board']:
            return False, "No piece at specified node"
        
        # Check if the piece is an enemy piece
        piece_name = self.game_state['board'][node_id]
        enemy_color = 'blue' if player['color'] == 'red' else 'red'
        if not piece_name.startswith(enemy_color + '_'):
            return False, "Can only control enemy pieces"
        
        # Check if the piece is a matron mother (cannot be controlled)
        if 'matron mother' in piece_name:
            return False, "Cannot control the matron mother"
        
        # Set the controlled piece
        self.game_state['controlled_piece_node'] = node_id
        self.game_state['controlled_piece_name'] = piece_name
        self.game_state['controlled_piece_original_color'] = enemy_color
        
        # Create move record
        self.game_state['last_move'] = {
            'move_type': 'enemy_piece_controlled',
            'player': player['color'],
            'controlled_piece': piece_name,
            'controlled_node': node_id
        }
        
        # Notify all players about the control
        notify_lobby_update(self.lobby_id, 'enemy_piece_controlled', self.game_state)
        
        return True, self.game_state
    
    def execute_controlled_move(self, from_node, to_node, player_id):
        """Execute a move with a controlled enemy piece."""
        # Verify the game has started
        if not self.game_state['game_started']:
            return False, "Game not started"
        
        # Find the player
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player:
            return False, "Player not found"
        
        # Check if player has a controlled piece
        if not self.game_state.get('controlled_piece_node'):
            return False, "No piece under control"
        
        if self.game_state.get('spider_control_player') != player['color']:
            return False, "Not your controlled piece"
        
        # Verify the from_node matches the controlled piece
        if from_node != self.game_state['controlled_piece_node']:
            return False, "Can only move the controlled piece"
        
        # Get the controlled piece info
        piece_name = self.game_state['controlled_piece_name']
        original_color = self.game_state['controlled_piece_original_color']
        
        # For controlled pieces, get legal moves with spider control enabled
        basic_legal_moves = get_legal_moves(piece_name, from_node, self.game_state['board'], original_color, spider_control=True)
        
        # Check if the move is in the basic legal moves
        if to_node not in basic_legal_moves:
            return False, "Invalid move for this piece type"
        
        # Use the existing execute_move logic but with spider control mode
        # Temporarily update the piece name to match the controlling player's color for execution
        temp_piece_name = f"{player['color']}_{piece_name.split('_', 1)[1]}"
        original_piece_name = self.game_state['board'][from_node]
        self.game_state['board'][from_node] = temp_piece_name
        
        # Execute the move using existing logic
        success, result = self.execute_move(from_node, to_node, player_id)
        
        if not success:
            # Restore original piece name if move failed
            self.game_state['board'][from_node] = original_piece_name
            return False, result
        
        # Restore the original piece color after the move
        if '->' in to_node:
            # Handle complex moves (weaponmaster/wizard)
            if 'weaponmaster' in piece_name:
                nodes = to_node.split('->')
                final_position = nodes[1] if len(nodes) == 2 else to_node
            elif 'wizard' in piece_name:
                nodes = to_node.split('->')
                final_position = nodes[2] if len(nodes) == 3 else to_node
            else:
                final_position = to_node
        else:
            final_position = to_node
        
        # Restore original piece name at final position
        self.game_state['board'][final_position] = piece_name
        captured_pieces = result['last_move'].get('captured', [])
        
        # Update game state
        self.game_state['last_move'] = {
            'from': from_node,
            'to': final_position,
            'piece': piece_name,
            'captured': captured_pieces,
            'player': player['color'],
            'move_type': 'controlled_piece_move',
            'original_piece_color': original_color
        }
        
        # Clear spider control mode and controlled piece
        self.game_state['spider_control_mode'] = False
        self.game_state['spider_control_player'] = None
        self.game_state['controlled_piece_node'] = None
        self.game_state['controlled_piece_name'] = None
        self.game_state['controlled_piece_original_color'] = None
        
        # Update timer and increment the current player's turn counter
        self._update_turn_timer(player['color'])
        self.game_state['player_turn_numbers'][player['color']] += 1
        
        # Switch turns after controlled move
        next_player_color = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
        self.game_state['current_turn'] = next_player_color
        self._start_next_player_timer(next_player_color)

        # Notify all players about the move
        notify_lobby_update(self.lobby_id, 'controlled_piece_moved', self.game_state)
        
        return True, self.game_state
    
    def promote_orc(self, player_id, selected_piece):
        """Promote an orc to a selected piece from captured pieces."""
        # Verify the game has started
        if not self.game_state['game_started']:
            return False, "Game not started"
        
        # Find the player
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player:
            return False, "Player not found"
        
        # Check if we're in promotion mode
        if not self.game_state.get('promotion_mode', False):
            return False, "Not in promotion mode"
        
        # Check if it's the correct player
        if self.game_state.get('promotion_player') != player['color']:
            return False, "Not your promotion"
        
        # Check if the selected piece is available for promotion
        # We look in the enemy's captured pieces (our own pieces that were captured)
        enemy_color = 'blue' if player['color'] == 'red' else 'red'
        captured_pieces = self.game_state['captured_pieces'][enemy_color]
        promotable_pieces = get_promotable_pieces(captured_pieces)
        
        if selected_piece not in promotable_pieces:
            return False, "Selected piece not available for promotion"
        
        # Remove the selected piece from the enemy's captured pieces (they lose the captured piece)
        self.game_state['captured_pieces'][enemy_color].remove(selected_piece)
        
        # Replace the orc with the promoted piece
        promotion_node = self.game_state['promotion_node']
        self.game_state['board'][promotion_node] = selected_piece
        
        # Create move record
        self.game_state['last_move'] = {
            'move_type': 'orc_promotion',
            'player': player['color'],
            'promoted_from': self.game_state['promotion_orc'],
            'promoted_to': selected_piece,
            'promotion_node': promotion_node
        }
        
        # Clear promotion mode
        self.game_state['promotion_mode'] = False
        self.game_state['promotion_player'] = None
        self.game_state['promotion_node'] = None
        self.game_state['promotion_orc'] = None
        
        # Update timer and increment the current player's turn counter
        self._update_turn_timer(player['color'])
        self.game_state['player_turn_numbers'][player['color']] += 1
        
        # Switch turns after promotion
        next_player_color = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
        self.game_state['current_turn'] = next_player_color
        self._start_next_player_timer(next_player_color)
        
        # Notify all players about the promotion
        notify_lobby_update(self.lobby_id, 'orc_promoted', self.game_state)
        
        return True, self.game_state
    
    def add_chat_message(self, player_id, message):
        """Add a chat message to the lobby."""
        # Find the player
        player = next((p for p in self.players if p['id'] == player_id), None)
        if not player:
            # Check spectators
            player = next((s for s in self.spectators if s['id'] == player_id), None)
            if not player:
                return False, "Player not found"
        
        # Create message object
        chat_message = {
            'id': len(self.game_state['chat_messages']) + 1,
            'player_id': player_id,
            'player_name': player['name'],
            'player_color': player.get('color', 'spectator'),
            'message': message,
            'timestamp': datetime.now().isoformat(),
            'is_spectator': player_id in [s['id'] for s in self.spectators]
        }
        
        # Add to chat messages (keep last 50 messages)
        self.game_state['chat_messages'].append(chat_message)
        if len(self.game_state['chat_messages']) > 50:
            self.game_state['chat_messages'] = self.game_state['chat_messages'][-50:]
        
        return True, chat_message
    
    def _is_move_safe_for_matron_mother(self, from_node, to_node, piece_name, player_color):
        """Check if a move would put the player's own Matron Mother in check."""
        # Create a temporary board state to simulate the move
        temp_board = self.game_state['board'].copy()
        
        # Handle special moves (weaponmaster, wizard)
        if '->' in to_node:
            nodes = to_node.split('->')
            if 'weaponmaster' in piece_name and len(nodes) == 2:
                # Weaponmaster move - remove piece from start, place at end
                del temp_board[from_node]
                temp_board[nodes[1]] = piece_name
                # Remove any captured pieces
                if nodes[0] in temp_board:
                    del temp_board[nodes[0]]
                if nodes[1] in temp_board:
                    del temp_board[nodes[1]]
                temp_board[nodes[1]] = piece_name
            elif 'wizard' in piece_name and len(nodes) == 3:
                # Wizard move - remove piece from start, place at end
                del temp_board[from_node]
                temp_board[nodes[2]] = piece_name
                # Remove any captured pieces
                if nodes[2] in temp_board:
                    del temp_board[nodes[2]]
                temp_board[nodes[2]] = piece_name
        else:
            # Regular move
            del temp_board[from_node]
            temp_board[to_node] = piece_name
        
        # Find the matron mother's position after the move
        matron_mother_node = None
        for node, piece in temp_board.items():
            if piece == f"{player_color}_matron mother":
                matron_mother_node = node
                break
        
        if not matron_mother_node:
            # Matron Mother not found - this shouldn't happen in a valid game
            return False
        
        # Check if any enemy piece can capture the matron mother
        enemy_color = 'blue' if player_color == 'red' else 'red'
        for node, piece in temp_board.items():
            if piece.startswith(enemy_color + '_'):
                # Get all possible moves for this enemy piece
                legal_moves = get_legal_moves(piece, node, temp_board, enemy_color)
                
                # Check if any of these moves would capture the matron mother
                if matron_mother_node in legal_moves:
                    return False
        
        return True
    
    def _is_player_in_check(self, player_color):
        """Check if a player is currently in check."""
        # Find the player's Matron Mother
        matron_mother_node = None
        for node, piece in self.game_state['board'].items():
            if piece == f"{player_color}_matron mother":
                matron_mother_node = node
                break
        
        if not matron_mother_node:
            # Matron Mother not found - this shouldn't happen in a valid game
            return False
        
        # Check if any enemy piece can capture the Matron Mother
        enemy_color = 'blue' if player_color == 'red' else 'red'
        for node, piece in self.game_state['board'].items():
            if piece.startswith(enemy_color + '_'):
                # Get all possible moves for this enemy piece
                legal_moves = get_legal_moves(piece, node, self.game_state['board'], enemy_color)
                
                # For wizard moves, only check the final destination, not intermediate nodes
                if 'wizard' in piece:
                    # Filter wizard moves to only include final destinations
                    filtered_moves = set()
                    for move in legal_moves:
                        if '->' in move:
                            # Wizard move with path - only include final destination
                            nodes = move.split('->')
                            if len(nodes) == 3:
                                filtered_moves.add(nodes[2])  # Final destination only
                        else:
                            # Single node move
                            filtered_moves.add(move)
                    legal_moves = filtered_moves
                
                # Check if any of these moves would capture the Matron Mother
                if matron_mother_node in legal_moves:
                    return True
        
        return False
    
    def _get_threatening_pieces(self, player_color):
        """Get list of enemy pieces that are threatening the player's Matron Mother."""
        threatening_pieces = []
        
        # Find the player's Matron Mother
        matron_mother_node = None
        for node, piece in self.game_state['board'].items():
            if piece == f"{player_color}_matron mother":
                matron_mother_node = node
                break
        
        if not matron_mother_node:
            return threatening_pieces
        
        # Check each enemy piece
        enemy_color = 'blue' if player_color == 'red' else 'red'
        for node, piece in self.game_state['board'].items():
            if piece.startswith(enemy_color + '_'):
                # Get all possible moves for this enemy piece
                legal_moves = get_legal_moves(piece, node, self.game_state['board'], enemy_color)
                
                # For wizard moves, only check the final destination, not intermediate nodes
                if 'wizard' in piece:
                    # Filter wizard moves to only include final destinations
                    filtered_moves = set()
                    for move in legal_moves:
                        if '->' in move:
                            # Wizard move with path - only include final destination
                            nodes = move.split('->')
                            if len(nodes) == 3:
                                filtered_moves.add(nodes[2])  # Final destination only
                        else:
                            # Single node move
                            filtered_moves.add(move)
                    legal_moves = filtered_moves
                
                # Check if this piece can capture the Matron Mother
                if matron_mother_node in legal_moves:
                    threatening_pieces.append({
                        'node_id': node,
                        'piece_name': piece,
                        'piece_type': piece.split('_')[1]
                    })
        
        return threatening_pieces
    
    def _does_move_resolve_check(self, from_node, to_node, piece_name, player_color):
        """Check if a move resolves the current check."""
        # Create a temporary board state to simulate the move
        temp_board = self.game_state['board'].copy()
        
        # Handle special moves (weaponmaster, wizard)
        if '->' in to_node:
            nodes = to_node.split('->')
            if 'weaponmaster' in piece_name and len(nodes) == 2:
                # Weaponmaster move - remove piece from start, place at end
                del temp_board[from_node]
                temp_board[nodes[1]] = piece_name
                # Remove any captured pieces
                if nodes[0] in temp_board:
                    del temp_board[nodes[0]]
                if nodes[1] in temp_board:
                    del temp_board[nodes[1]]
                temp_board[nodes[1]] = piece_name
            elif 'wizard' in piece_name and len(nodes) == 3:
                # Wizard move - remove piece from start, place at end
                del temp_board[from_node]
                temp_board[nodes[2]] = piece_name
                # Remove any captured pieces
                if nodes[2] in temp_board:
                    del temp_board[nodes[2]]
                temp_board[nodes[2]] = piece_name
        else:
            # Regular move
            del temp_board[from_node]
            temp_board[to_node] = piece_name
        
        # Find the matron mother's position after the move
        matron_mother_node = None
        for node, piece in temp_board.items():
            if piece == f"{player_color}_matron mother":
                matron_mother_node = node
                break
        
        if not matron_mother_node:
            # Matron Mother not found - this shouldn't happen in a valid game
            return False
        
        # Check if any enemy piece can still capture the Matron Mother after the move
        enemy_color = 'blue' if player_color == 'red' else 'red'
        for node, piece in temp_board.items():
            if piece.startswith(enemy_color + '_'):
                # Get all possible moves for this enemy piece
                legal_moves = get_legal_moves(piece, node, temp_board, enemy_color)
                
                # For wizard moves, only check the final destination, not intermediate nodes
                if 'wizard' in piece:
                    # Filter wizard moves to only include final destinations
                    filtered_moves = set()
                    for move in legal_moves:
                        if '->' in move:
                            # Wizard move with path - only include final destination
                            nodes = move.split('->')
                            if len(nodes) == 3:
                                filtered_moves.add(nodes[2])  # Final destination only
                        else:
                            # Single node move
                            filtered_moves.add(move)
                    legal_moves = filtered_moves
                
                # Check if any of these moves would capture the Matron Mother
                if matron_mother_node in legal_moves:
                    return False
        
        return True
    
    def _is_player_in_checkmate(self, player_color):
        """Check if a player is in checkmate (in check with no legal moves)."""
        if not self._is_player_in_check(player_color):
            return False
        
        # Check if any piece of this color has any legal moves that resolve the check
        for node_id, piece_name in self.game_state['board'].items():
            if piece_name.startswith(player_color + '_'):
                # Get basic legal moves for this piece
                basic_legal_moves = get_legal_moves(piece_name, node_id, self.game_state['board'], player_color)
                
                # Check if any of these moves resolve the check
                for move in basic_legal_moves:
                    if self._does_move_resolve_check(node_id, move, piece_name, player_color):
                        return False
        
        return True
    
    def _does_player_have_legal_moves(self, player_color):
        """Check if a player has any legal moves available."""
        total_basic_moves = 0
        total_legal_moves = 0
        
        for node_id, piece_name in self.game_state['board'].items():
            if piece_name.startswith(player_color + '_'):
                # Get basic legal moves for this piece
                basic_legal_moves = get_legal_moves(piece_name, node_id, self.game_state['board'], player_color)
                total_basic_moves += len(basic_legal_moves)

                # If not in check, filter out moves that would put own Matron Mother in check
                if not self._is_player_in_check(player_color):
                    for move in basic_legal_moves:
                        if self._is_move_safe_for_matron_mother(node_id, move, piece_name, player_color):
                            total_legal_moves += 1
                            return True
                else:
                    # If in check, check if any move resolves the check
                    for move in basic_legal_moves:
                        if self._does_move_resolve_check(node_id, move, piece_name, player_color):
                            total_legal_moves += 1
                            return True
        return False

# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('join_lobby')
def handle_join_lobby(data):
    lobby_id = data.get('lobby_id')
    
    if lobby_id in lobbies:
        join_room(lobby_id)

        # Send current lobby state to the joining player
        lobby_info = lobbies[lobby_id].get_lobby_info()
        # Convert datetime objects to strings for JSON serialization
        lobby_info = json.loads(json.dumps(lobby_info, default=str))
        emit('lobby_update', {
            'event_type': 'joined_lobby',
            'lobby_info': lobby_info
        })
        
        # Check if we should auto-start the game after this player joins
        lobby = lobbies[lobby_id]
        if len(lobby.players) == 2 and not lobby.game_state['game_started']:
            lobby.auto_start_game()

@socketio.on('leave_lobby')
def handle_leave_lobby(data):
    lobby_id = data.get('lobby_id')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        leave_room(lobby_id)
        
        # Remove player from lobby
        lobby = lobbies[lobby_id]
        should_cleanup = lobby.remove_player(player_id)
        
        if should_cleanup:
            del lobbies[lobby_id]
        else:
            # Notify remaining players
            notify_lobby_update(lobby_id, 'player_left', {'player_id': player_id})

@socketio.on('sacrifice_piece')
def handle_sacrifice_piece(data):
    lobby_id = data.get('lobby_id')
    node_id = data.get('node_id')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        
        # Sacrifice the piece
        success, result = lobby.sacrifice_piece(node_id, player_id)
        
        if success:
            # Notify all players about the sacrifice
            notify_lobby_update(lobby_id, 'piece_sacrificed', result)
        else:
            # Send error back to the player
            emit('sacrifice_error', {'error': result})
    else:
        emit('sacrifice_error', {'error': 'Lobby not found'})

@socketio.on('control_enemy_piece')
def handle_control_enemy_piece(data):
    lobby_id = data.get('lobby_id')
    node_id = data.get('node_id')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        
        # Control the enemy piece
        success, result = lobby.control_enemy_piece(node_id, player_id)
        
        if success:
            # Notify all players about the control
            notify_lobby_update(lobby_id, 'enemy_piece_controlled', result)
        else:
            # Send error back to the player
            emit('spider_control_error', {'error': result})
    else:
        emit('spider_control_error', {'error': 'Lobby not found'})

@socketio.on('move_controlled_piece')
def handle_move_controlled_piece(data):
    lobby_id = data.get('lobby_id')
    from_node = data.get('from_node')
    to_node = data.get('to_node')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        
        # Execute the controlled move
        success, result = lobby.execute_controlled_move(from_node, to_node, player_id)
        
        if success:
            # Notify all players about the controlled move
            notify_lobby_update(lobby_id, 'controlled_piece_moved', result)
        else:
            # Send error back to the player
            emit('controlled_move_error', {'error': result})
    else:
        emit('controlled_move_error', {'error': 'Lobby not found'})

@socketio.on('send_chat_message')
def handle_send_chat_message(data):
    lobby_id = data.get('lobby_id')
    message = data.get('message')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        
        # Add the chat message
        success, result = lobby.add_chat_message(player_id, message)
        
        if success:
            # Notify all players about the new message
            notify_lobby_update(lobby_id, 'chat_message_sent', result)
        else:
            # Send error back to the player
            emit('chat_error', {'error': result})
    else:
        emit('chat_error', {'error': 'Lobby not found'})

@socketio.on('promote_orc')
def handle_promote_orc(data):
    lobby_id = data.get('lobby_id')
    selected_piece = data.get('selected_piece')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        
        # Promote the orc
        success, result = lobby.promote_orc(player_id, selected_piece)
        
        if success:
            # Notify all players about the promotion
            notify_lobby_update(lobby_id, 'orc_promoted', result)
        else:
            # Send error back to the player
            emit('promotion_error', {'error': result})
    else:
        emit('promotion_error', {'error': 'Lobby not found'})

@socketio.on('player_timeout')
def handle_player_timeout(data):
    lobby_id = data.get('lobby_id')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        
        # Find the player
        player = next((p for p in lobby.players if p['id'] == player_id), None)
        if not player:
            emit('timeout_error', {'error': 'Player not found'})
            return
        
        # Handle the timeout
        success, result = lobby.handle_player_timeout(player['color'])
        
        if success:
            # Timeout notification already sent in handle_player_timeout
            pass
        else:
            # Send error back to the player
            emit('timeout_error', {'error': result})
    else:
        emit('timeout_error', {'error': 'Lobby not found'})

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/rules')
def rules():
    return render_template('rules.html')

@app.route('/lobbies')
def lobby_list():
    return render_template('lobby_list.html')

@app.route('/game')
def game():
    return redirect(url_for('create_lobby'))

@app.route('/create-lobby')
def create_lobby():
    lobby_id = str(uuid.uuid4())[:8]  # Short lobby ID
    lobbies[lobby_id] = Lobby(lobby_id)
    return redirect(url_for('join_lobby', lobby_id=lobby_id))

@app.route('/lobby/<lobby_id>')
def join_lobby(lobby_id):
    if lobby_id not in lobbies:
        return render_template('error.html', message="Lobby not found")
    
    lobby = lobbies[lobby_id]
    return render_template('lobby.html', lobby_id=lobby_id)

@app.route('/api/lobby/<lobby_id>/join', methods=['POST'])
def join_lobby_api(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    player_id = data.get('player_id')
    player_name = data.get('player_name', f'Player {player_id[:4]}')
    
    lobby = lobbies[lobby_id]
    role = lobby.add_player(player_id, player_name)

    # Notify all players about the new player
    notify_lobby_update(lobby_id, 'player_joined', {'player_id': player_id, 'role': role})
    
    return jsonify({
        'role': role,
        'lobby_info': lobby.get_lobby_info()
    })

@app.route('/api/lobby/<lobby_id>/leave', methods=['POST'])
def leave_lobby_api(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    player_id = data.get('player_id')
    
    lobby = lobbies[lobby_id]
    should_cleanup = lobby.remove_player(player_id)
    
    if should_cleanup:
        del lobbies[lobby_id]
    else:
        # Notify remaining players
        notify_lobby_update(lobby_id, 'player_left', {'player_id': player_id})
    
    return jsonify({'success': True})

@app.route('/api/lobby/<lobby_id>/state')
def get_lobby_state(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    lobby = lobbies[lobby_id]
    return jsonify(lobby.get_lobby_info())

@app.route('/api/lobby/<lobby_id>/legal-moves/<node_id>')
def get_legal_moves_api(lobby_id, node_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    lobby = lobbies[lobby_id]
    legal_moves = lobby.get_legal_moves_for_piece(node_id)
    
    return jsonify({
        'legal_moves': legal_moves,
        'current_turn': lobby.game_state['current_turn']
    })

@app.route('/api/lobby/<lobby_id>/move', methods=['POST'])
def move_piece_api(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    from_node = data.get('from_node')
    to_node = data.get('to_node')
    player_id = data.get('player_id')
    
    lobby = lobbies[lobby_id]
    
    # Verify player is in this lobby
    player = next((p for p in lobby.players if p['id'] == player_id), None)
    if not player:
        return jsonify({'error': 'Player not in lobby'}), 403
    
    # Verify game is started
    if not lobby.game_state['game_started']:
        return jsonify({'error': 'Game not started'}), 400
    
    # Verify it's the player's turn
    if player['color'] != lobby.game_state['current_turn']:
        return jsonify({'error': 'Not your turn'}), 400
    
    # Execute the move
    success, result = lobby.execute_move(from_node, to_node, player_id)
    
    if success:
        return jsonify({
            'success': True,
            'game_state': result,
            'lobby_info': lobby.get_lobby_info()
        })
    else:
        return jsonify({'error': result}), 400

@app.route('/api/lobby/<lobby_id>/roll-spider-dice', methods=['POST'])
def roll_spider_dice_api(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    player_id = data.get('player_id')
    
    if not player_id:
        return jsonify({'error': 'Player ID required'}), 400
    
    lobby = lobbies[lobby_id]
    
    # Verify player is in this lobby
    player = next((p for p in lobby.players if p['id'] == player_id), None)
    if not player:
        return jsonify({'error': 'Player not in lobby'}), 403
    
    # Roll the spider dice
    success, result = lobby.roll_spider_dice(player_id)
    
    if success:
        return jsonify({
            'success': True,
            'game_state': result,
            'lobby_info': lobby.get_lobby_info()
        })
    else:
        return jsonify({'error': result}), 400

@app.route('/api/lobby/<lobby_id>/sacrifice', methods=['POST'])
def sacrifice_piece_api(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    node_id = data.get('node_id')
    player_id = data.get('player_id')
    
    if not node_id or not player_id:
        return jsonify({'error': 'Node ID and Player ID required'}), 400
    
    lobby = lobbies[lobby_id]
    
    # Verify player is in this lobby
    player = next((p for p in lobby.players if p['id'] == player_id), None)
    if not player:
        return jsonify({'error': 'Player not in lobby'}), 403
    
    # Sacrifice the piece
    success, result = lobby.sacrifice_piece(node_id, player_id)
    
    if success:
        return jsonify({
            'success': True,
            'game_state': result
        })
    else:
        return jsonify({'error': result}), 400

@app.route('/api/lobby/<lobby_id>/check-status', methods=['GET'])
def check_status_api(lobby_id):
    """Check if a player is in check and return threatening pieces."""
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    lobby = lobbies[lobby_id]
    
    # Check if game is started
    if not lobby.game_state['game_started']:
        return jsonify({'error': 'Game not started'}), 400
    
    # Get player color from query parameter or use current turn
    player_color = request.args.get('player', lobby.game_state['current_turn'])
    
    # Check if player is in check
    is_in_check = lobby._is_player_in_check(player_color)
    
    # Find threatening pieces if in check
    threatening_pieces = []
    if is_in_check:
        threatening_pieces = lobby._get_threatening_pieces(player_color)
    
    return jsonify({
        'player': player_color,
        'is_in_check': is_in_check,
        'threatening_pieces': threatening_pieces
    })

@app.route('/api/lobby/<lobby_id>/check-move', methods=['POST'])
def check_move_api(lobby_id):
    """Check if a move would result in check for the Matron Mother."""
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    board_state = data.get('board_state', {})
    target_node = data.get('target_node')
    enemy_color = data.get('enemy_color')
    
    if not target_node or not enemy_color:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    # Check if any enemy piece can capture the target node (Matron Mother)
    for node_id, piece_name in board_state.items():
        if piece_name.startswith(enemy_color + '_'):
            # Get all possible moves for this enemy piece
            legal_moves = get_legal_moves(piece_name, node_id, board_state, enemy_color)
            
            # Check if any of these moves would capture the target
            if target_node in legal_moves:
                return jsonify({
                    'would_result_in_check': True,
                    'threatening_piece': piece_name,
                    'threatening_node': node_id,
                    'target_node': target_node
                })
    
    return jsonify({
        'would_result_in_check': False
    })

@app.route('/api/lobby/<lobby_id>/update-state', methods=['POST'])
def update_game_state(lobby_id):
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    player_id = data.get('player_id')
    game_state = data.get('game_state')
    
    lobby = lobbies[lobby_id]
    
    # Verify player is in this lobby
    player = next((p for p in lobby.players if p['id'] == player_id), None)
    if not player:
        return jsonify({'error': 'Player not in lobby'}), 403
    
    # Update game state
    lobby.game_state.update(game_state)
    
    return jsonify({'success': True})

@app.route('/api/game-config')
def get_game_config():
    """Return game configuration constants."""
    config = {
        'max_players': MAX_PLAYERS,
        'auto_start_threshold': AUTO_START_THRESHOLD,
        'turn_time_limit_seconds': TURN_TIME_LIMIT,
        'board_connections': BOARD_CONNECTIONS
    }
    return jsonify(config)

@app.route('/api/lobbies')
def get_all_lobbies():
    """Return list of all active lobbies."""
    lobby_list = []
    
    for lobby_id, lobby in lobbies.items():
        lobby_info = {
            'lobby_id': lobby_id,
            'player_count': len(lobby.players),
            'spectator_count': len(lobby.spectators),
            'max_players': MAX_PLAYERS,
            'can_join': len(lobby.players) < MAX_PLAYERS,
            'game_started': lobby.game_state.get('game_started', False),
            'game_over': lobby.game_state.get('game_over', False),
            'current_turn': lobby.game_state.get('current_turn'),
            'created_at': lobby.created_at.isoformat(),
            'players': [{'name': p['name'], 'color': p['color']} for p in lobby.players]
        }
        lobby_list.append(lobby_info)
    
    # Sort by creation time (newest first)
    lobby_list.sort(key=lambda x: x['created_at'], reverse=True)
    
    return jsonify({
        'lobbies': lobby_list,
        'total_count': len(lobby_list)
    })

@app.route('/api/lobby/<lobby_id>/chat', methods=['POST'])
def send_chat_message_api(lobby_id):
    """Send a chat message to the lobby."""
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    message = data.get('message')
    player_id = data.get('player_id')
    
    if not message or not player_id:
        return jsonify({'error': 'Message and Player ID required'}), 400
    
    if len(message.strip()) == 0:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    if len(message) > 500:
        return jsonify({'error': 'Message too long (max 500 characters)'}), 400
    
    lobby = lobbies[lobby_id]
    
    # Add the chat message
    success, result = lobby.add_chat_message(player_id, message.strip())
    
    if success:
        # Notify all players about the new message via WebSocket
        notify_lobby_update(lobby_id, 'chat_message_sent', result)
        
        return jsonify({
            'success': True,
            'message': result
        })
    else:
        return jsonify({'error': result}), 400

@app.route('/api/lobby/<lobby_id>/promote-orc', methods=['POST'])
def promote_orc_api(lobby_id):
    """Promote an orc to a selected piece."""
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    selected_piece = data.get('selected_piece')
    player_id = data.get('player_id')
    
    if not selected_piece or not player_id:
        return jsonify({'error': 'Selected piece and Player ID required'}), 400
    
    lobby = lobbies[lobby_id]
    
    # Verify player is in this lobby
    player = next((p for p in lobby.players if p['id'] == player_id), None)
    if not player:
        return jsonify({'error': 'Player not in lobby'}), 403
    
    # Promote the orc
    success, result = lobby.promote_orc(player_id, selected_piece)
    
    if success:
        return jsonify({
            'success': True,
            'game_state': result,
            'lobby_info': lobby.get_lobby_info()
        })
    else:
        return jsonify({'error': result}), 400

@app.route('/api/lobby/<lobby_id>/timeout', methods=['POST'])
def player_timeout_api(lobby_id):
    """Handle player timeout."""
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    data = request.get_json()
    player_id = data.get('player_id')
    
    if not player_id:
        return jsonify({'error': 'Player ID required'}), 400
    
    lobby = lobbies[lobby_id]
    
    # Verify player is in this lobby
    player = next((p for p in lobby.players if p['id'] == player_id), None)
    if not player:
        return jsonify({'error': 'Player not in lobby'}), 403
    
    # Handle the timeout
    success, result = lobby.handle_player_timeout(player['color'])
    
    if success:
        return jsonify({
            'success': True,
            'game_state': result,
            'lobby_info': lobby.get_lobby_info()
        })
    else:
        return jsonify({'error': result}), 400

if __name__ == '__main__':
    # Development server - use gunicorn for production
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    
    socketio.run(app, debug=debug_mode, host=host, port=port) 