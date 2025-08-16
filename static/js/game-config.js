// Shared game configuration - loads from JSON file for consistency with backend
let GAME_CONFIG = null;

// Load configuration from JSON file
async function loadGameConfig() {
    try {
        const response = await fetch('/static/game-config.json');
        if (!response.ok) {
            throw new Error(`Failed to load game config: ${response.status}`);
        }
        GAME_CONFIG = await response.json();
        console.log('Game configuration loaded successfully');
        return GAME_CONFIG;
    } catch (error) {
        console.error('Error loading game configuration:', error);
        return {};
    }
}

// Helper functions for game logic
const GameLogic = {
    // Utility functions that don't duplicate backend logic
    getPieceSymbol: function(pieceName) {
        if (!GAME_CONFIG || !pieceName) return pieceName;
        
        const parts = pieceName.split('_');
        const color = parts[0];
        
        // Handle orc pieces (they have names like "red_orc_0", "blue_orc_1")
        if (pieceName.includes('orc')) {
            return GAME_CONFIG.piece_types[color].orc;
        }
        
        // For other pieces, join the remaining parts (e.g., "matron mother")
        const pieceType = parts.slice(1).join('_');
        return GAME_CONFIG.piece_types[color][pieceType] || pieceName;
    },

    isEnemyPiece: function(pieceName, playerColor) {
        if (!pieceName) return false;
        return pieceName.startsWith(playerColor === 'red' ? 'blue_' : 'red_');
    },

    getConfig: function(key, defaultValue = null) {
        if (!GAME_CONFIG) return defaultValue;
        return key.split('.').reduce((obj, k) => obj && obj[k], GAME_CONFIG) ?? defaultValue;
    },

    // Get strand node arrays (for backward compatibility)
    getStrandNodes: function() {
        if (!GAME_CONFIG) return [];
        return GAME_CONFIG.strand_definitions.map(strand => strand.nodes);
    },

    // Get strand definitions with full metadata
    getStrandDefinitions: function() {
        if (!GAME_CONFIG) return [];
        return GAME_CONFIG.strand_definitions;
    }
};

// Initialize configuration when module loads
loadGameConfig();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { GAME_CONFIG, GameLogic, loadGameConfig };
} 