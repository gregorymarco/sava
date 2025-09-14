# Description

This repo is for a chess-like turn-based game called "Sava". It's a molecular-themed strategy game with unique piece mechanics and special abilities.

# Technical Overview

## Architecture
- **Backend**: Flask (Python) server in `app.py` - handles all game logic, WebSocket connections, and API endpoints
- **Frontend**: HTML5, CSS3, JavaScript with HTMX for dynamic updates
- **Real-time**: Socket.IO for WebSocket communication between players
- **Deployment**: Production-ready with Gunicorn, includes deployment scripts for Render

## File Structure
- **Templates**: HTML components in `templates/` directory
    - `lobby.html` - Main game interface (165KB, 3448 lines)
    - `sidebar_component.html` - Player info and game controls
    - `chat_component.html` - Real-time chat functionality
    - `rules.html` - Game rules documentation
    - Other UI components for landing, lobby list, etc.
- **Static Assets**: 
    - `static/css/lobby.css` - All styling (responsive design, mobile-optimized)
    - `static/js/game-config.js` - Client-side game configuration loader
    - `static/game-config.json` - Server-side game constants and board definitions
- **Server**: `app.py` - Main Flask application with game logic, WebSocket handlers, and API routes

## Key Features
- **Responsive Design**: Mobile-first approach with adaptive layouts
- **Real-time Multiplayer**: WebSocket-based lobby system with live updates
- **Game Mechanics**: 
    - Molecular board with concentric rings and connecting strands
    - Special pieces (Weaponmaster, Wizard, Orc promotion)
    - Spider dice system with special effects (piece control, sacrifice)
    - Check/checkmate detection
- **Development Tools**: Built-in dev mode with debugging features and keyboard shortcuts

## Development Guidelines
- **CSS Changes**: Add all styling to `static/css/lobby.css` (not inline)
- **Game Constants**: Store configurable values in `static/game-config.json`
- **Client-side Changes**: Templates are client-side - changes should be low-trust
- **Mobile Considerations**: All UI components are mobile-responsive with touch-friendly interactions
- **WebSocket Events**: Real-time game updates use Socket.IO events for moves, chat, dice rolls, etc.