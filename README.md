# Sava

A turn-based chess-like web game built with Flask and HTMX.

Visit it live in a free render container! sava.onrender.com

## Setup

## Dev Setup
1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the server:
```bash
python3 app.py
```

3. Open your browser and navigate to:
```
http://localhost:5000
```

## Production Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the server:
```bash
./start_production.sh
```

3. Open your browser and navigate to:
```
http://localhost:5000
```

## Current Status

- ~~🔄 In dev~~
- 🎉 Prototype! A fully legal game technically can be played over the board, with a few small bugs. Testing is underway...

## TODOs

Next steps:
- Matchmaking
- Stored game/account state 
    - Match history
    - Elo

## Technologies Used

- **Backend**: Flask (Python)
- **Frontend**: HTML5, CSS3, JavaScript
- **Dynamic Updates**: HTMX
- **Styling**: Custom CSS
