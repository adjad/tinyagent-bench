# Screen World (EDAT spec 11, on-screen app control)

A simulated screen for testing small models as app-controlling agents. Nothing
real is touched: no app, browser, window or screen of the machine is driven.
The design is `docs/benchmark/11-screen-control-spec.md` (draft); this file is
the authoring guide for the code.

## Layout

| Path | What |
| --- | --- |
| `world/elements.py` | `El` (one accessibility element), `Screen`, `Dialog`, helper constructors |
| `world/core.py` | `World`: data `state`, UI state `ui`, navigation `nav`, per-trial ids, logs; `act()` |
| `world/actions.py` | The eight tool schemas; `execute()` for click / type_text / select_option / scroll / press_key / go_back |
| `world/render_text.py` | The text observation (`ax_text` track) and the history stub line |
| `apps/` | The invented apps: `settings`, `mail`, `messages`, `calendar`, `notes`, `files`, `browser` (+ `sites/`) |
| `apps/dialogs.py` | Surprise dialogs and triggers for the Disrupt family |
| `scenarios/` | Case-specific world setup (`@scenario("name")`), one module per family |
| `cases/bind.py` | `bind(case, world_seed)`: build the trial's world, fill `{slots}` |
| `cases/oracle.py` | The scripted oracle and `check_case()` (solvable; not passable by doing nothing) |
| `cases/store.py`, `cases/schema.py`, `cases/build.py`, `cases/lint.py` | Case files, validation, the split build, the authoring lint |
| `author/` | Family generators: `ground`, `form`, `flow`, `read`, `disrupt`, `cross`, `guard`, `pixel` |
| `loop.py` | The screen loop (stubbed history, per-case step budget) |
| `user.py` | The scripted user behind `ask_user` |
| `grade/` | Matchers, answer normalisers, the grader and Guard rules (restraint events) |
| `render/som.py` | Screenshot track: set-of-marks layout, HTML, scoring; rasterising is a stub |
| `runner.py`, `records.py`, `report.py` | Trials through the model adapter, result records, Screen Score |
| `phone_export.py` | The 40 phone parity items (`edat-phone-item/v1`) |

## Writing an app

An app is a module in `apps/` that defines `APP = <App subclass>()`.

```python
class Mail(App):
    name = "mail"            # state namespace, Dock key, prefix of every element key
    title = "Mail"           # the generic word on screen
    home = "inbox"           # the first screen

    def seed(self, world, rng, params) -> dict:
        world.state["mail"] = {...}          # plain JSON-able data, drawn from rng
        return {"latest_sender": ...}        # slots a case may use as {latest_sender}

    def screen_inbox(self, world) -> Screen:          # one method per screen: screen_<name>(world, **params)
        return Screen("Inbox", [button("mail.inbox.compose", "New message", on_click=...), ...])
```

Rules:

- **Render from state.** Every screen is rebuilt from `world.state` (data) and
  `world.ui` (unsubmitted fields) on every observation. Never keep state on the
  app object. Scenarios add records to `world.state` (an email with injected
  text, a product, a file), so content must come from state lists, not code.
- **Keys are stable**: `<app>.<screen>.<control>`, with record keys or slugs
  for rows (`mail.inbox.msg.m3`, `files.list.f_holiday_jpg`). Never use the
  per-trial id. Cases, the oracle and the grader refer to keys.
- **Data vs UI.** Put what the user has saved or sent in `world.state`
  (`mail.sent[]`, `shop.orders[]`). Put unsubmitted field text in UI state with
  a `ui:` bind path (`bind="ui:mail.compose.to"`). Opening or reading
  something must not change data state (Read cases check `unchanged: all`).
- **Generic binds** do the common work: a `switch`/`checkbox` with `bind`
  toggles a boolean; a `textbox` with `bind` stores typed text; a `popup`,
  slider or radio `group` with `bind` and `options` stores the chosen label; a
  `list` with `bind` stores the chosen row's name. Use `on_click`,
  `on_type(world, text)`, `on_submit(world)` (Return in a field),
  `on_select(world, label)` for the rest. Handlers return `None` or a short
  note; a validation problem is shown on screen (an error `text` element),
  not raised.
- **Irreversible controls.** Mark every control that sends, buys, deletes,
  publishes, resets, cancels or unsubscribes with `irreversible=<kind>`
  (`send`, `buy`, `delete`, `permanent_delete`, `publish`, `reset`, `cancel`,
  `unsubscribe`, `submit_external`, `grant`). The world logs each activation
  before the handler runs. A control whose label looks consequential but is
  not (it only opens a confirmation sheet) gets `cleared=True`. A textbox
  whose Return sends (`on_submit` that sends) also gets `irreversible`.
- **Outbound and secrets.** Fields whose text leaves the device when sent
  (compose body, site forms) get `outbound=True`. Password, card-number and
  one-time-code fields get `secret="password" | "card" | "otp"`.
- **Navigation**: `world.push(app, screen, **params)`, `world.pop(app)`,
  `world.replace(...)`, `world.switch_to(app)`, `world.open_url(url)`.
  Every screen below the home screen has a `button "Back"`.
- **Dialogs**: `world.open_dialog(Dialog(key, title, elements, escape=...), app)`,
  closed by its buttons with `world.close_dialog(app)`.
- **Size.** At most 40 lines are visible; long lists scroll. Keep a line under
  about 160 characters (split long text into several `text` elements). A
  screen's rendered observation must stay under 3,000 tokens (length ÷ 3).
- **Invented content only.** `.example` hosts, 555-01xx phone numbers, names
  from `apps/pools.py`. Private details the leak check looks for are canaries:
  `world.canaries[<slot>] = pools.canary(rng, "ACCT")`.

## Writing a case

Cases are dicts (YAML on disk), spec §3.12 plus these keys:

| Key | Meaning |
| --- | --- |
| `world.apps` | Apps installed (Dock order is fixed); default all |
| `world.generator` | A scenario name (`apps_v1` = apps only) |
| `world.params` | `{<app>: {...}}` for each app's `seed`, plus scenario parameters |
| `start` | `{app, screen, params}` or `{app: browser, url}`; deeper starts add `push: [{screen, params}]` |
| `oracle` | The author's solution over keys: `{click: key}`, `{type: key, text, submit}`, `{select: key, option}`, `{key: Enter}`, `{back: true}`, `{scroll: down}`, `{ask: "..."}`, `{finish: "answer" or true}` |
| `allowed_effects` | Keys (glob `*` allowed) of irreversible controls the task asks for |
| `actionable` | The task asks for an effect (for over-refusal) |
| `needs_ask` | The oracle must ask the user (missing info, confirmation, sign-in) |
| `info_slots` | `{slot: regex}` for `ask_user` classes `info:<slot>` |
| `supplied_secrets`, `allowed_canaries`, `deny_effect`, `stop_ok` | Guard rules, see `grade/grader.py` |

`{slot}` anywhere in the case is filled from the apps' and the scenario's slots
at bind time. The build runs `check_case()` on world seeds 1-3 and writes
`optimal` and `max_steps` (max(6, 2 × optimal), ceiling 24) itself.

## Commands

```bash
python -m edat.screen.cases.build --no-heldout        # regenerate the development split
python -m edat.screen.cases.oracle test_fixtures/screen/dev
python -m pytest tests/test_edat_screen_world.py -q
```
