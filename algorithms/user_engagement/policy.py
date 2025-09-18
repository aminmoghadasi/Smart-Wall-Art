import random, sqlite3, os
from typing import Tuple

DB_PATH = os.getenv("DB_PATH", "storage/feedback.db")

PALETTES = [
    {'bg': (0,0,0), 'fg': (255,255,255)},
    {'bg': (20,20,60), 'fg': (220,240,255)},
    {'bg': (240,230,210), 'fg': (10,20,40)},
    {'bg': (10,50,40), 'fg': (250,240,200)},
]

def get_recent_avg(n: int = 20) -> float:
    if not os.path.exists(DB_PATH): return 3.0
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT rating FROM feedback ORDER BY id DESC LIMIT ?", (n,))
        vals = [r[0] for r in cur.fetchall()]
        return sum(vals)/len(vals) if vals else 3.0

def choose_palette(current: dict | None, eps: float = 0.2) -> Tuple[dict, bool]:
    changed = False
    if current is None or random.random() < eps:
        base = random.choice(PALETTES)
        tries = 0
        while current is not None and base == current and tries < 5:
            base = random.choice(PALETTES); tries += 1
        current = base
        changed = True
    return current, changed

def should_explore(threshold: float = 3.0) -> float:
    avg = get_recent_avg()
    # lower avg -> increase epsilon (explore more)
    if avg >= threshold + 0.5: return 0.1
    if avg >= threshold:       return 0.2
    if avg >= threshold - 0.5: return 0.3
    return 0.5
