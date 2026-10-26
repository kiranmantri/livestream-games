"""Generate docs/diagrams/physical-architecture.drawio (Live Games on Fly.io). Run: python3 docs/diagrams/make_physical.py"""
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(__file__).with_name("physical-architecture.drawio")
cells: list[str] = []

PEOPLE = "fillColor=#dae8fc;strokeColor=#6c8ebf;"
FLY = "fillColor=#e1d5e7;strokeColor=#9673a6;"
APP = "fillColor=#d5e8d4;strokeColor=#82b366;"
DATA = "fillColor=#fff2cc;strokeColor=#d6b656;"
EXT = "fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;"
BOX = "rounded=1;whiteSpace=wrap;html=1;arcSize=8;fontSize=12;"
LANE = "swimlane;startSize=30;html=1;whiteSpace=wrap;rounded=0;fontSize=12;fontStyle=1;container=1;pointerEvents=0;"
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;fontSize=11;labelBackgroundColor=#ffffff;endArrow=block;endFill=1;"


def v(id_, label, style, x, y, w, h, parent="1"):
    cells.append(f'<mxCell id="{id_}" value={quoteattr(label)} style="{style}" vertex="1" parent="{parent}">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def e(id_, src, tgt, label="", style="", points=(), lx=None, off=None):
    """lx: label position along the edge (-1 source .. 1 target); off: (dx, dy) label offset in px."""
    pts = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
    inner = (f'<Array as="points">{pts}</Array>' if pts else "") + (f'<mxPoint x="{off[0]}" y="{off[1]}" as="offset"/>' if off else "")
    xattr = f' x="{lx}"' if lx is not None else ""
    geo = f'<mxGeometry{xattr} relative="1" as="geometry">{inner}</mxGeometry>'
    cells.append(f'<mxCell id="{id_}" value={quoteattr(label)} style="{EDGE}{style}" edge="1" parent="1" source="{src}" target="{tgt}">{geo}</mxCell>')


# ---- title
v("title", "<b>Live Games: physical architecture</b> (Fly.io app <i>livebingo</i>, https://livebingo.fly.dev)",
  "text;html=1;align=left;verticalAlign=middle;fontSize=18;", 40, 20, 1100, 40)

# ---- people (left column)
v("players", "<b>Players</b><br>browser / phone<br>pages poll with HTMX", BOX + PEOPLE, 40, 240, 200, 80)
v("admin", "<b>Host / admin</b><br>browser, /admin<br>(HTTP Basic)", BOX + PEOPLE, 40, 320 + 10, 200, 60)
v("operator", "<b>Operator laptop</b><br>fly CLI, scripts/fly-*.sh<br>data/backup/*.sql", BOX + PEOPLE, 40, 480, 200, 90)

# ---- Fly edge
v("proxy", "<b>Fly.io edge proxy</b><br>TLS (HTTPS)<br>adds Fly-Client-IP", BOX + FLY, 320, 250, 180, 150)

# ---- Fly region / Machine / containers (absolute coordinates converted to parent-relative below)
RX, RY = 560, 140
v("region", "Fly.io region gru (São Paulo)", LANE + FLY + "fillColor=#f7f2fa;", RX, RY, 740, 840)
MX, MY = 580, 180
v("machine", "Fly Machine: shared-cpu-1x, 1 GB RAM (ONE Machine, containers share localhost)", LANE + FLY, MX - RX, MY - RY, 700, 650, "region")
AX, AY = 800, 220
v("appc", "container <b>app</b>: python:3.14-slim, uv, non-root, read-only rootfs", LANE + APP + "fillColor=#eef7ec;", AX - MX, AY - MY, 460, 500, "machine")
v("web", "<b>Web server</b><br>FastHTML + HTMX<br>uvicorn :8000<br><br>Bingo, Quem é Falacioso?,<br>Aponte o mentiroso!,<br>/admin, /healthz",
  BOX + APP + "verticalAlign=top;spacingTop=8;", 820 - AX, 270 - AY, 180, 200, "appc")
v("workers", "<b>Background workers</b> (threads)<br><br>"
             "• live-stream detector (yt-dlp)<br>• replay collector (yt-dlp)<br>• live-chat worker (yt-dlp)<br>"
             "• speech-to-text worker<br>&nbsp;&nbsp;(yt-dlp | ffmpeg, 12 s chunks)<br>• fact-check workers ×2<br>&nbsp;&nbsp;(pydantic-ai agent)",
  BOX + APP + "verticalAlign=top;align=left;spacingLeft=10;spacingTop=8;", 1030 - AX, 270 - AY, 210, 430, "appc")
DX, DY = 600, 480
v("dbc", "container <b>db</b>: postgres:17", LANE + DATA + "fillColor=#fffaeb;", DX - MX, DY - MY, 180, 200, "machine")
v("pg", "<b>PostgreSQL 17</b><br>127.0.0.1:5432<br>database livegames", "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;size=12;fontSize=12;" + DATA,
  620 - DX, 530 - DY, 140, 120, "dbc")
v("vol", "<b>Volume pgdata</b> 3 GB<br>/data/pgdata, encrypted<br>daily snapshots (5 days)", "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;size=12;fontSize=11;" + DATA,
  610 - MX, 710 - MY, 160, 100, "machine")
v("secrets", "<b>Fly secrets</b><br>DB password + URL, ADMIN_PASSWORD,<br>SESSION_SECRET, API keys<br>(Groq, OpenRouter)", BOX + FLY + "fontSize=11;", 580 - RX, 880 - RY, 240, 80, "region")
v("registry", "<b>registry.fly.io/livebingo</b><br>image built from ./Dockerfile by fly deploy", BOX + FLY, 840 - RX, 880 - RY, 420, 80, "region")

# ---- external providers (right column), each centred on the worker exit it receives
PX, PW = 1400, 260
v("yt", "<b>YouTube</b> (@O_Poligrafo)<br>live detection, live chat,<br>live audio (HLS), replays<br>+ the embedded player", BOX + EXT, PX, 245, PW, 110)
v("groq", "<b>Groq API</b><br>whisper-large-v3-turbo (speech-to-text)", BOX + EXT, PX, 375, PW, 70)
v("xai", "<b>xAI API</b><br>grok speech-to-text (alternative, off)", BOX + EXT + "dashed=1;", PX, 465, PW, 60)
v("openrouter", "<b>OpenRouter API</b><br>openai/gpt-oss-120b (fact-checker LLM)", BOX + EXT, PX, 545, PW, 70)
v("ddg", "<b>DuckDuckGo</b> (ddgs)<br>web search for the fact-checker", BOX + EXT, PX, 635, PW, 70)
v("extcap", "External services, all called over HTTPS<br>from the Machine (outbound only)", "text;html=1;align=center;verticalAlign=top;fontSize=11;fontColor=#666666;", PX, 715, PW, 40)

# ---- legend (bottom right)
v("legend", "Legend", "rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#666666;verticalAlign=top;fontStyle=1;fontSize=12;", PX, 790, PW, 150)
for i, (name, style) in enumerate([("People / clients", PEOPLE), ("Fly.io platform", FLY), ("App container", APP), ("Database + storage", DATA), ("External service", EXT)]):
    v(f"lg{i}", "", "rounded=0;html=1;" + style, 10, 30 + 22 * i, 30, 14, "legend")
    v(f"lg{i}t", name, "text;html=1;align=left;verticalAlign=middle;fontSize=11;", 50, 26 + 22 * i, 200, 22, "legend")

# ---- edges (exit/entry pinned so every line is straight or routed through an empty corridor)
H = "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryDy=0;"
e("e1", "players", "proxy", "HTTPS", H + "entryY=0.2;")
e("e2", "admin", "proxy", "HTTPS", H + "entryY=0.733;")
e("e3", "proxy", "web", "HTTP :8000", "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.275;entryDx=0;entryDy=0;")
e("e4", "web", "pg", "SQL<br>(psycopg pool)", "exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=1;entryY=0.5;entryDx=0;entryDy=0;labelBackgroundColor=none;", [(910, 590)],
  lx=-0.45, off=(48, 0))
e("e5", "pg", "vol", "", "exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;")
e("e6", "operator", "dbc", "fly ssh: pg_dump / restore", "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.225;entryDx=0;entryDy=0;")
e("e7", "operator", "registry", "fly deploy (build + push)", "exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=1;entryDx=0;entryDy=0;", [(140, 1010), (1050, 1010)])
e("e8", "registry", "appc", "image", "exitX=0.5;exitY=0;exitDx=0;exitDy=0;entryX=0.543;entryY=1;entryDx=0;entryDy=0;")
e("e9", "secrets", "machine", "env at boot", "exitX=0.875;exitY=0;exitDx=0;exitDy=0;entryX=0.3;entryY=1;entryDx=0;entryDy=0;dashed=1;", off=(-40, 0))
W = "exitX=1;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
e("w1", "workers", "yt", "yt-dlp", lx=0.1, style=W + "exitY=0.0698;")
e("w2", "workers", "groq", "audio chunks", lx=0.1, style=W + "exitY=0.3256;")
e("w3", "workers", "xai", "(off)", lx=0.1, style=W + "exitY=0.5233;dashed=1;")
e("w4", "workers", "openrouter", "LLM calls", lx=0.1, style=W + "exitY=0.7209;")
e("w5", "workers", "ddg", "search", lx=0.1, style=W + "exitY=0.9302;")
e("e10", "players", "yt", "video embed (iframe, straight from YouTube)", "exitX=0.5;exitY=0;exitDx=0;exitDy=0;entryX=0.77;entryY=0;entryDx=0;entryDy=0;dashed=1;",
  [(140, 100), (1600, 100)])

xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="drawio" version="26.0.0"><diagram name="Physical architecture">'
       '<mxGraphModel dx="1700" dy="1060" grid="1" gridSize="10" page="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
       + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
OUT.write_text(xml, encoding="utf-8")
print(OUT)
