"""Tables from probe.py's JSON (GUI audit, step 2).

    python scripts/audit/gui/probe_table.py probe.json            # per screen, top calls/s
    python scripts/audit/gui/probe_table.py probe.json --hidden   # what runs on Home of other screens
    python scripts/audit/gui/probe_table.py probe.json --screen "manual: run" --top 40
"""
import argparse
import json


def rows(shot, top, least=1.0):
    seconds = shot["seconds"]
    out = []
    for name, (calls, own, cum) in shot["calls"].items():
        rate = calls / seconds
        if rate >= least:
            out.append((rate, name, own / seconds, cum / seconds))
    out.sort(reverse=True)
    return out[:top]


def show(title, shot, top):
    print(f"\n### {title}  ({shot['seconds']:.0f} s)")
    print("| calls/s | own ms/s | with callees ms/s | what |")
    print("|---:|---:|---:|---|")
    for rate, name, own, cum in rows(shot, top):
        print(f"| {rate:.1f} | {own:.2f} | {cum:.2f} | `{name}` |")
    setters = sorted(((v / shot["seconds"], k) for k, v in shot["setters"].items()),
                     reverse=True)
    for rate, key in setters[:12]:
        if rate >= 0.5:
            print(f"- setter {key}: {rate:.1f}/s")
    events = sorted(((v / shot["seconds"], k) for k, v in shot["events"].items()),
                    reverse=True)
    for rate, key in events[:15]:
        if rate >= 1:
            print(f"- event {key}: {rate:.1f}/s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--top", type=int, default=14)
    parser.add_argument("--screen", default="")
    parser.add_argument("--hidden", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    data = json.load(open(args.file))
    if args.summary:
        print("| screen | Python ms per s, idle | active | paints/s idle | active | camera ticks/s | rebuilds/s |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for name, shots in data["screens"].items():
            def total(shot):
                return sum(own for name, (_c, own, _cum) in shot["calls"].items()
                           if not name.endswith(".processEvents")) / shot["seconds"]

            def rate(shot, needle):
                return sum(c for n, (c, _o, _cu) in shot["calls"].items()
                           if n.endswith(needle)) / shot["seconds"]
            idle, active = shots["idle"], shots["active"]
            print(f"| {name} | {total(idle):.0f} | {total(active):.0f} | "
                  f"{rate(idle, ' paintEvent'):.0f} | {rate(active, ' paintEvent'):.0f} | "
                  f"{rate(idle, ' _tick'):.0f} | {rate(idle, ' _rebuild'):.0f} |")
        return
    if args.hidden:
        show("Home shown, every other screen hidden", data["hidden"], args.top)
        return
    for name, shots in data["screens"].items():
        if args.screen and name != args.screen:
            continue
        show(f"{name}, idle", shots["idle"], args.top)
        show(f"{name}, active (jog keys at 20 Hz)", shots["active"], args.top)


if __name__ == "__main__":
    main()
