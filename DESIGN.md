---
version: alpha
name: Jogos da Live
description: A dark, calm stage for playing along with a live stream - the stream is the star, the games are quiet and quick to tap.
colors:
  primary: "#38BDF8"
  secondary: "#94A3B8"
  tertiary: "#F59E0B"
  neutral: "#0F172A"
  surface: "#1E293B"
  surface-raised: "#273449"
  surface-input: "#0B1220"
  line: "#334155"
  text: "#E2E8F0"
  on-primary: "#082F49"
  on-tertiary: "#1C1203"
  success: "#22C55E"
  on-success: "#052E16"
  danger: "#F87171"
  on-danger: "#450A0A"
typography:
  display:
    fontFamily: Inter
    fontSize: 1.75rem
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  h1:
    fontFamily: Inter
    fontSize: 1.3rem
    fontWeight: 700
    lineHeight: 1.2
  h2:
    fontFamily: Inter
    fontSize: 1.05rem
    fontWeight: 600
    lineHeight: 1.3
  body-md:
    fontFamily: Inter
    fontSize: 1rem
    lineHeight: 1.4
  body-sm:
    fontFamily: Inter
    fontSize: 0.875rem
    lineHeight: 1.4
  cell:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: 500
    lineHeight: 1.2
  label-caps:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: 600
    letterSpacing: "0.08em"
rounded:
  sm: 8px
  md: 12px
  lg: 16px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
components:
  page:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.text}"
    typography: "{typography.body-md}"
  divider:
    backgroundColor: "{colors.line}"
    height: 1px
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    height: 44px
    padding: 12px
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    height: 44px
    padding: 12px
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.on-danger}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    height: 44px
    padding: 12px
  badge-live:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.on-tertiary}"
    typography: "{typography.label-caps}"
    rounded: "{rounded.full}"
    padding: 4px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: 16px
  input:
    backgroundColor: "{colors.surface-input}"
    textColor: "{colors.text}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    height: 44px
    padding: 12px
  cell:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.cell}"
    rounded: "{rounded.sm}"
    padding: 4px
  cell-marked:
    backgroundColor: "{colors.success}"
    textColor: "{colors.on-success}"
---

## Overview

Jogos da Live is a companion to a YouTube live stream (pt-BR debate channel). People play on a phone with one
eye on the video, so the interface must be glanceable, forgiving, and fast: one tap per action, large targets,
no reading required while the stream is moving. The mood is a dim studio - dark surfaces, a single cool accent for
things you can press, a warm accent only for "happening now".

This file is the TARGET identity. `app/static/app.css` already implements the dark palette (neutral, surface, line,
text, primary, success, danger) and the 12 px panels; the amber accent, the Inter type scale, the 8 px spacing
rhythm and the 44 px control height are not all in the CSS yet.

## Colors

- **Primary ({colors.primary}):** the only colour for things you press (main buttons, links, tab underline, my
  score). One bright cyan per screen region keeps the eye on the next action.
- **Tertiary ({colors.tertiary}):** amber, reserved for "live / now" (the live badge, the newest pending item in the
  admin queue, the "so close" hint on a nearly complete line). Never for buttons.
- **Neutral ({colors.neutral}), Surface, Surface-raised, Surface-input:** three depth levels plus an inset for text
  fields. Cards sit on `surface`; menus and the picker drawer on `surface-raised`.
- **Secondary ({colors.secondary}):** helper text, timestamps, labels. Never for anything the player must read to act.
- **Success ({colors.success}):** only "done": a marked cell, a correct verdict. **Danger ({colors.danger}):** only
  destructive or failed: end game, delete, error boxes. Never use either for decoration.
- **Line ({colors.line}):** 1 px borders and dividers.

## Typography

Inter everywhere (self-hosted woff2, `font-display: swap`; system-ui as fallback). Hierarchy comes from size and
weight, not from extra families. Body is 16 px so iOS does not zoom inputs. Bingo cells use 13 px minimum
(`cell`), with the trailing "(falácia...)" tag as a smaller second line. Timestamps in chat and transcript use
`body-sm` in the secondary colour. Names in chat use a stable per-author colour, never the primary cyan.

## Layout

An 8 px rhythm (`spacing.sm`); `md` inside components, `lg` between blocks, `xl` between page sections. Content
is one column up to 560 px on phones (my card FIRST, then video/chat, then other players) and a two-column stage on
desktop: stream left, my card right, the others' cards in a ranked strip below. Admin pages are wide (up to 1100 px)
with tables that become labelled cards below 760 px. Nothing may scroll sideways; every control is at least 44 px
tall (`height`), and thumbs reach the primary action: game tabs live in a bottom bar on phones.

## Elevation & Depth

Dark UIs look flat when every panel is the same colour, so depth is colour first: `neutral` < `surface` <
`surface-raised`. Add only a faint top highlight (1 px lighter edge) to raised surfaces, and a soft shadow for the
picker drawer and the sticky status bar. No heavy drop shadows.

## Shapes

Modest, friendly rounding: `sm` (8 px) on buttons, inputs and Bingo cells, `md` (12 px) on panels, `lg` (16 px) on
the picker drawer and dialogs, `full` for pills (live badge, counters). My own card gets a 2 px primary border so
"mine" is unmistakable; other players' cards are smaller and read-only.

## Components

- `button-primary` is the one high-emphasis action per region ("Entrar", "Enviar", "Continuar"). `button-secondary`
  is for everything else (hide video, refresh). `button-danger` is only for "Encerrar partida", "Excluir", and always
  asks for confirmation.
- `panel` is the default container. Inside a panel, headings use `h2`; a panel never contains another panel.
- `cell` is a tap target: pressed state scales to 0.97 for 80 ms; `cell-marked` shows a check badge in the corner and
  the time marked. A line one cell from completion gets a thin tertiary outline.
- `badge-live` appears next to a game tab whose session is running (replaces the plain green dot).
- Chat/transcript lines read `name [HH:MM:SS]: text`; the name and time are NOT selectable text.
- Admin verdict buttons are semantic: Correto = success colours, Errado = amber outline, Spam = secondary. Each shows
  its keyboard key (C, E, S) as a small badge.

## Do's and Don'ts

- **Do** keep every interaction to one tap and show the result immediately (optimistic flip, then confirm).
- **Do** give failing embeds (YouTube chat unavailable) a dark placeholder with an "Abrir no YouTube" button instead
  of the embed's white error box.
- **Do** keep copy in pt-BR from `app/texts.py`; never hard-code strings in views.
- **Don't** use more than one primary-coloured control in the same region, and never use `tertiary` for buttons.
- **Don't** introduce colours outside this palette; extend it here first.
- **Don't** add a JavaScript framework or any server-side JS (no Node at runtime): the server is FastHTML + HTMX.
  Small hand-written client-side scripts and CSS animations are welcome where they make the UI feel alive (tap
  feedback, drawers, toasts, tab state); keep each under ~100 lines and progressive: the page must still work without them.
- **Don't** rely on colour alone for state: marked cells also show a check, errors also show text.
