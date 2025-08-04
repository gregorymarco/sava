# Chess-like Game with HTMX

A turn-based chess-like web game built with Flask and HTMX.

## Features

- Modern, responsive UI with beautiful gradients
- Interactive 8x8 game board
- HTMX integration for dynamic updates
- Flask backend with RESTful API
- Real-time game state management

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the server:
```bash
python app.py
```

3. Open your browser and navigate to:
```
http://localhost:5000
```

## Project Structure

```
sava/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── templates/          # HTML templates
│   └── index.html     # Main game interface
└── README.md          # This file
```

## Current Status

- ✅ Basic Flask server setup
- ✅ Hello world page with HTMX
- ✅ Interactive game board UI
- ✅ API endpoint for testing
- 🔄 Game logic implementation (next step)

## Next Steps

1. Implement game pieces and their movements
2. Add turn-based gameplay
3. Create game state management
4. Add win conditions
5. Implement multiplayer support

## Technologies Used

- **Backend**: Flask (Python)
- **Frontend**: HTML5, CSS3, JavaScript
- **Dynamic Updates**: HTMX
- **Styling**: Custom CSS with modern design 