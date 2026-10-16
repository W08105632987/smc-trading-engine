"""Deterministic SYNTHETIC tick generator for software regression ONLY (not market evidence).
Volatility clustering + liquidity-run-then-impulse patterns so sweeps/displacement/MSS/zones/decisions occur."""
import random, sys
from datetime import datetime, timedelta, timezone

def generate(path, days=3, seed=11, step_s=10):
    rnd = random.Random(seed)
    t = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    end = t + timedelta(days=days)
    price = 2400.0
    vol = 0.30
    mode = "range"; mode_left = 0; drift = 0.0; lvl = price
    lines = ["Timestamp,Bid price,Ask price,Bid volume,Ask volume"]
    i = 0
    while t < end:
        if mode_left <= 0:
            r = rnd.random()
            if r < 0.45:   mode, mode_left, drift = "range", rnd.randint(150, 500), 0.0
            elif r < 0.65: mode, mode_left, drift = "runlow", rnd.randint(40, 90), -0.35     # liquidity run down
            elif r < 0.85: mode, mode_left, drift = "impulse_up", rnd.randint(25, 60), 0.9    # displacement
            elif r < 0.93: mode, mode_left, drift = "runhigh", rnd.randint(40, 90), 0.35
            else:          mode, mode_left, drift = "impulse_dn", rnd.randint(25, 60), -0.9
            lvl = price
        mode_left -= 1
        vol = max(0.12, min(1.4, vol * (1 + rnd.uniform(-0.04, 0.045))))
        if mode == "range":
            price += rnd.uniform(-vol, vol) + 0.04 * (lvl - price)
        else:
            price += drift * vol + rnd.uniform(-vol, vol) * 0.6
        spread = 0.25 + (0.1 if rnd.random() < 0.05 else 0.0)
        lines.append(f"{t:%Y%m%d %H:%M:%S}:000,{price:.3f},{price + spread:.3f},0.1,0.1")
        t += timedelta(seconds=step_s)
        i += 1
    open(path, "w").write("\n".join(lines))
    return i

if __name__ == "__main__":
    n = generate(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    print("ticks", n)
