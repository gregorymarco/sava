from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid
import json
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

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

def get_legal_moves_for_orc(node_id, board_state, current_color):
    """Calculate legal moves for an Orc piece."""
    legal_moves = set()
    neighbors = get_neighboring_nodes(node_id)
    
    for neighbor_id in neighbors:
        # Skip if neighbor is occupied by own piece
        if neighbor_id in board_state:
            piece_name = board_state[neighbor_id]
            if piece_name.startswith(current_color + '_'):
                continue
        
        # Check if this move would move away from enemy pieces
        if neighbor_id in board_state:
            # This is a capture move - always legal
            legal_moves.add(neighbor_id)
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

def get_legal_moves_for_priestess(node_id, board_state, current_color):
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
                # Check if we can capture (enemy piece)
                piece_name = board_state[target_node]
                if is_enemy_piece(piece_name, current_color):
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
                # Check if we can capture (enemy piece)
                piece_name = board_state[target_node]
                if is_enemy_piece(piece_name, current_color):
                    legal_moves.add(target_node)
                # Stop checking this direction (can't move through pieces)
                break
            else:
                # Empty node, can move here
                legal_moves.add(target_node)
    
    return list(legal_moves)

def get_legal_moves_for_matron_mother(node_id, board_state, current_color):
    """Calculate legal moves for a Matron Mother piece."""
    legal_moves = set()
    
    # Get all neighboring nodes
    neighbors = get_neighboring_nodes(node_id)
    
    for neighbor_id in neighbors:
        # Skip if neighbor is occupied by own piece
        if neighbor_id in board_state:
            piece_name = board_state[neighbor_id]
            if piece_name.startswith(current_color + '_'):
                continue
        
        # Check if this move would put the king in check
        # TODO: Implement check evaluation
        # For now, allow all moves
        is_in_check_after_move = False  # Placeholder for check evaluation
        
        if not is_in_check_after_move:
            legal_moves.add(neighbor_id)
    
    return list(legal_moves)

def get_legal_moves_for_weaponmaster(node_id, board_state, current_color):
    """Calculate legal moves for a Weaponmaster piece."""
    legal_moves = set()
    
    # Get all neighboring nodes for the first move
    first_neighbors = get_neighboring_nodes(node_id)
    
    for first_neighbor_id in first_neighbors:
        # Skip if first neighbor is occupied by own piece (can't move through friendly pieces)
        if first_neighbor_id in board_state:
            piece_name = board_state[first_neighbor_id]
            if piece_name.startswith(current_color + '_'):
                continue
        
        # Get neighbors of the first neighbor for the second move
        second_neighbors = get_neighboring_nodes(first_neighbor_id)
        for second_neighbor_id in second_neighbors:
            # Skip if it's the original starting position (can't return to start)
            if second_neighbor_id == node_id:
                continue
            
            # Skip if occupied by own piece (can't move through friendly pieces on second move)
            if second_neighbor_id in board_state:
                piece_name = board_state[second_neighbor_id]
                if piece_name.startswith(current_color + '_'):
                    continue
            
            # Add the complete two-node path as a legal move
            # Format: "first_node->second_node" to represent the complete move
            move_path = f"{first_neighbor_id}->{second_neighbor_id}"
            legal_moves.add(move_path)
    
    return list(legal_moves)

def get_legal_moves_for_wizard(node_id, board_state, current_color):
    """Calculate legal moves for a Wizard piece."""
    legal_moves = set()
    
    # Get all neighboring nodes for the first move
    first_neighbors = get_neighboring_nodes(node_id)
    
    for first_neighbor_id in first_neighbors:
        # Skip if first neighbor is the starting position
        if first_neighbor_id == node_id:
            continue
            
        # Wizard can move through any pieces (friendly or enemy) - no restrictions on first move
        
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
                
                # Wizard can move through any pieces on third move too
                
                # Add the complete three-node path as a legal move
                # Format: "first_node->second_node->third_node" to represent the complete move
                move_path = f"{first_neighbor_id}->{second_neighbor_id}->{third_neighbor_id}"
                legal_moves.add(move_path)
    
    return list(legal_moves)

def get_legal_moves(piece_name, node_id, board_state, current_color):
    """Get legal moves for any piece type."""
    if 'orc' in piece_name:
        return get_legal_moves_for_orc(node_id, board_state, current_color)
    elif 'priestess' in piece_name:
        return get_legal_moves_for_priestess(node_id, board_state, current_color)
    elif 'matron mother' in piece_name:
        return get_legal_moves_for_matron_mother(node_id, board_state, current_color)
    elif 'weaponmaster' in piece_name:
        return get_legal_moves_for_weaponmaster(node_id, board_state, current_color)
    elif 'wizard' in piece_name:
        return get_legal_moves_for_wizard(node_id, board_state, current_color)
    else:
        # For other pieces, return all neighboring nodes (placeholder)
        neighbors = get_neighboring_nodes(node_id)
        legal_moves = []
        for neighbor_id in neighbors:
            if neighbor_id not in board_state or is_enemy_piece(board_state[neighbor_id], current_color):
                legal_moves.append(neighbor_id)
        return legal_moves

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
        self.game_state = {
            'board': {},
            'current_turn': 'red',
            'game_started': False,
            'last_move': None,
            'game_pieces': {},  # Will store piece positions
            'captured_pieces': {
                'red': [],  # Pieces captured by red player
                'blue': []  # Pieces captured by blue player
            }
        }

    def add_player(self, player_id, player_name):
        if len(self.players) < 2:
            self.players.append({
                'id': player_id,
                'name': player_name,
                'color': 'red' if len(self.players) == 0 else 'blue',
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
        
        # Update the board state to reflect piece positions
        self.game_state['board'] = {}
        for piece_name, node_id in piece_mapping.items():
            self.game_state['board'][node_id] = piece_name
        
        # Notify all players about the game start
        print(f"Notifying all players in lobby {self.lobby_id} about game start")
        notify_lobby_update(self.lobby_id, 'game_started', self.game_state)

    def auto_start_game(self):
        """Automatically start the game with default piece mapping when two players join."""
        print(f"Auto-starting game for lobby {self.lobby_id}")
        print(f"Players: {[p['id'] for p in self.players]}")
        
        # Use shared configuration for piece placement
        # Note: This would need to be loaded from the shared config file
        # For now, keeping the existing mapping but it should be moved to game-config.js
        piece_mapping = GAME_CONFIG["initial_piece_placement"]
        
        self.setup_game_board(piece_mapping)
        print(f"Game auto-started successfully for lobby {self.lobby_id}")

    def get_legal_moves_for_piece(self, node_id):
        """Get legal moves for a piece at the given node."""
        if not self.game_state['game_started']:
            return []
        
        if node_id not in self.game_state['board']:
            return []
        
        piece_name = self.game_state['board'][node_id]
        current_color = piece_name.split('_')[0]
        
        # Check if it's the current player's turn
        if current_color != self.game_state['current_turn']:
            return []
        
        # Get basic legal moves
        basic_legal_moves = get_legal_moves(piece_name, node_id, self.game_state['board'], current_color)
        
        # If the player is in check, filter moves to only include those that resolve the check
        if self._is_player_in_check(current_color):
            resolving_moves = []
            for move in basic_legal_moves:
                if self._does_move_resolve_check(node_id, move, piece_name, current_color):
                    resolving_moves.append(move)
            return resolving_moves
        
        # If not in check, filter out moves that would put the player in check
        safe_moves = []
        for move in basic_legal_moves:
            if self._is_move_safe_for_matron_mother(node_id, move, piece_name, current_color):
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
        print(f"DEBUG: Move validation - from_node: {from_node}, to_node: {to_node}")
        print(f"DEBUG: Legal moves: {legal_moves}")
        print(f"DEBUG: to_node in legal_moves: {to_node in legal_moves}")
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
                    print(f"Piece {captured_piece} captured on first node by {player['color']} player")
                
                # Check for captures on second node
                if second_node in self.game_state['board']:
                    captured_piece = self.game_state['board'][second_node]
                    captured_pieces.append(captured_piece)
                    self.game_state['captured_pieces'][player['color']].append(captured_piece)
                    # Remove the captured piece from the board
                    del self.game_state['board'][second_node]
                    print(f"Piece {captured_piece} captured on second node by {player['color']} player")
                
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
                
                print(f"Weaponmaster move completed. Captured pieces: {captured_pieces}")
                print(f"Total captured pieces for {player['color']}: {self.game_state['captured_pieces'][player['color']]}")
                print(f"Board state after weaponmaster move: {self.game_state['board']}")
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
                    print(f"Piece {captured_piece} captured on final node by {player['color']} player")
                
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
                
                print(f"Wizard three-node move completed. Captured pieces: {captured_pieces}")
                print(f"Total captured pieces for {player['color']}: {self.game_state['captured_pieces'][player['color']]}")
                print(f"Board state after wizard move: {self.game_state['board']}")
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
                print(f"Piece {captured_piece} captured by {player['color']} player")
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
        
        # Switch turns
        self.game_state['current_turn'] = 'blue' if self.game_state['current_turn'] == 'red' else 'red'
        
        # Check if the next player is in check
        next_player_color = self.game_state['current_turn']
        if self._is_player_in_check(next_player_color):
            print(f"⚠️  {next_player_color.upper()} player is in CHECK!")
            print(f"🎯 {next_player_color} player must make a move that resolves the check")
            
            # Check if they have any legal moves
            if not self._does_player_have_legal_moves(next_player_color):
                print(f"🚨 {next_player_color.upper()} player has NO LEGAL MOVES while in check!")
        
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
                
                # Check if any of these moves would capture the Matron Mother
                if matron_mother_node in legal_moves:
                    return True
        
        return False
    
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
        print(f"🔍 Checking legal moves for {player_color} player...")
        total_basic_moves = 0
        total_legal_moves = 0
        
        for node_id, piece_name in self.game_state['board'].items():
            if piece_name.startswith(player_color + '_'):
                # Get basic legal moves for this piece
                basic_legal_moves = get_legal_moves(piece_name, node_id, self.game_state['board'], player_color)
                total_basic_moves += len(basic_legal_moves)
                
                if basic_legal_moves:
                    print(f"  📍 {piece_name} at {node_id}: {len(basic_legal_moves)} basic moves")
                
                # If not in check, filter out moves that would put own Matron Mother in check
                if not self._is_player_in_check(player_color):
                    for move in basic_legal_moves:
                        if self._is_move_safe_for_matron_mother(node_id, move, piece_name, player_color):
                            total_legal_moves += 1
                            print(f"    ✅ Legal move: {node_id} -> {move}")
                            return True
                        else:
                            print(f"    ❌ Move blocked (would put Matron Mother in check): {node_id} -> {move}")
                else:
                    # If in check, check if any move resolves the check
                    for move in basic_legal_moves:
                        if self._does_move_resolve_check(node_id, move, piece_name, player_color):
                            total_legal_moves += 1
                            print(f"    ✅ Legal move (resolves check): {node_id} -> {move}")
                            return True
                        else:
                            print(f"    ❌ Move blocked (doesn't resolve check): {node_id} -> {move}")
        
        print(f"📊 {player_color} player: {total_basic_moves} basic moves, {total_legal_moves} legal moves")
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
    player_id = data.get('player_id')
    
    print(f'WebSocket: Player {player_id} joining lobby {lobby_id}')
    
    if lobby_id in lobbies:
        join_room(lobby_id)
        print(f'WebSocket: Player {player_id} joined room {lobby_id}')
        print(f'Current lobby state: {lobbies[lobby_id].get_lobby_info()}')
        
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
            print(f'Auto-starting game for lobby {lobby_id}')
            lobby.auto_start_game()
    else:
        print(f'WebSocket: Lobby {lobby_id} not found')

@socketio.on('leave_lobby')
def handle_leave_lobby(data):
    lobby_id = data.get('lobby_id')
    player_id = data.get('player_id')
    
    if lobby_id in lobbies:
        leave_room(lobby_id)
        print(f'Player {player_id} left lobby {lobby_id}')
        
        # Remove player from lobby
        lobby = lobbies[lobby_id]
        should_cleanup = lobby.remove_player(player_id)
        
        if should_cleanup:
            del lobbies[lobby_id]
        else:
            # Notify remaining players
            notify_lobby_update(lobby_id, 'player_left', {'player_id': player_id})

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/rules')
def rules():
    return render_template('rules.html')

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
    
    print(f"Player {player_id} joined lobby {lobby_id} as {role}")
    print(f"Current players: {[p['id'] for p in lobby.players]}")
    print(f"Current spectators: {[s['id'] for s in lobby.spectators]}")
    
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
            'game_state': result
        })
    else:
        return jsonify({'error': result}), 400

@app.route('/api/lobby/<lobby_id>/check-status', methods=['GET'])
def check_status_api(lobby_id):
    """Check if the current player is in check."""
    if lobby_id not in lobbies:
        return jsonify({'error': 'Lobby not found'}), 404
    
    lobby = lobbies[lobby_id]
    
    # Check if game is started
    if not lobby.game_state['game_started']:
        return jsonify({'error': 'Game not started'}), 400
    
    # Get current turn
    current_turn = lobby.game_state['current_turn']
    
    # Check if current player is in check
    is_in_check = lobby._is_player_in_check(current_turn)
    
    return jsonify({
        'current_turn': current_turn,
        'is_in_check': is_in_check
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
        'board_connections': BOARD_CONNECTIONS
    }
    return jsonify(config)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000) 