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
    // Get all neighboring nodes for a given node
    getNeighboringNodes: function(nodeId) {
        if (!GAME_CONFIG) return [];
        
        const neighbors = new Set();
        
        // Check ring connections
        if (nodeId.startsWith('R')) {
            const [ring, nodeNum] = nodeId.match(/R(\d+)N(\d+)/).slice(1);
            const ringIndex = parseInt(ring);
            const nodeIndex = parseInt(nodeNum);
            
            // Add adjacent nodes on the same ring
            const prevNode = (nodeIndex - 1 + 16) % 16;
            const nextNode = (nodeIndex + 1) % 16;
            neighbors.add(`R${ringIndex}N${prevNode}`);
            neighbors.add(`R${ringIndex}N${nextNode}`);
        }
        
        // Check center node connections
        if (nodeId.startsWith('C')) {
            const centerIndex = parseInt(nodeId[1]);
            // Center nodes form a diamond pattern based on strand connections:
            // C0 connects to C1 and C2
            // C1 connects to C0 and C3  
            // C2 connects to C0 and C3
            // C3 connects to C1 and C2
            if (centerIndex === 0) {  // C0
                neighbors.add('C1');  // Horizontal strand 2
                neighbors.add('C2');  // Vertical strand 1
            } else if (centerIndex === 1) {  // C1
                neighbors.add('C0');  // Horizontal strand 2
                neighbors.add('C3');  // Vertical strand 2
            } else if (centerIndex === 2) {  // C2
                neighbors.add('C0');  // Vertical strand 1
                neighbors.add('C3');  // Horizontal strand 1
            } else if (centerIndex === 3) {  // C3
                neighbors.add('C1');  // Vertical strand 2
                neighbors.add('C2');  // Horizontal strand 1
            }
        }
        
        // Check strand connections
        GAME_CONFIG.strand_definitions.forEach(strandDef => {
            const nodeIndex = strandDef.nodes.indexOf(nodeId);
            if (nodeIndex !== -1) {
                // Add nodes before and after on the strand
                if (nodeIndex > 0) {
                    neighbors.add(strandDef.nodes[nodeIndex - 1]);
                }
                if (nodeIndex < strandDef.nodes.length - 1) {
                    neighbors.add(strandDef.nodes[nodeIndex + 1]);
                }
            }
        });
        
        return Array.from(neighbors);
    },

    // Check if a piece belongs to the enemy
    isEnemyPiece: function(pieceName, playerColor) {
        return pieceName.startsWith(playerColor === 'red' ? 'blue_' : 'red_');
    },

    // Get piece symbol by name
    getPieceSymbol: function(pieceName) {
        if (!GAME_CONFIG) return pieceName;
        
        if (pieceName.startsWith('red_')) {
            const pieceType = pieceName.replace('red_', '');
            if (pieceType.startsWith('orc_')) {
                return GAME_CONFIG.piece_types.red.orc;
            }
            return GAME_CONFIG.piece_types.red[pieceType] || pieceName;
        } else if (pieceName.startsWith('blue_')) {
            const pieceType = pieceName.replace('blue_', '');
            if (pieceType.startsWith('orc_')) {
                return GAME_CONFIG.piece_types.blue.orc;
            }
            return GAME_CONFIG.piece_types.blue[pieceType] || pieceName;
        }
        return pieceName;
    },

    // Get configuration value with fallback
    getConfig: function(key, defaultValue = null) {
        if (!GAME_CONFIG) return defaultValue;
        
        const keys = key.split('.');
        let value = GAME_CONFIG;
        
        for (const k of keys) {
            if (value && typeof value === 'object' && k in value) {
                value = value[k];
            } else {
                return defaultValue;
            }
        }
        
        return value;
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