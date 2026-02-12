use anyhow::{anyhow, Context, Result};
use clap::Parser;
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    prelude::*,
    widgets::{block::Title, Block, Borders, Paragraph, Wrap},
};
use std::{
    io::{self, Stdout},
    time::Duration,
};

#[derive(Parser, Debug)]
#[command(author, version, about)]
struct Args {
    /// Display name/version shown in UI (e.g. "AirSim - Unreal Engine 4.27")
    #[arg(long, default_value = "AirSim - Unreal Engine <package version>")]
    target: String,

    /// If set, does not execute privileged commands; only shows what would run.
    #[arg(long)]
    dry_run: bool,
}

// Welcome screen branding.
// We render our logo from an embedded PNG so it scales to terminal size.
static LOGO_PNG: &[u8] = include_bytes!("../assets/logo.png");

fn render_logo_ascii(max_w: u16, max_h: u16) -> String {
    // Output is plain text (no ANSI) so it inherits the TUI's theme.
    // We use a small density ramp that looks decent in most monospace fonts.
    // (If you want stricter ASCII-only, swap these for " .:-=+*#%@".)
    let ramp: &[char] = &[' ', '░', '▒', '▓', '█'];

    let img = match image::load_from_memory_with_format(LOGO_PNG, image::ImageFormat::Png) {
        Ok(i) => i.to_rgba8(),
        Err(_) => return "(logo decode failed)".into(),
    };

    if max_w < 8 || max_h < 6 {
        return "".into();
    }

    // Terminals use rectangular cells. Compensate a bit so circles don't look squashed.
    // This factor is font-dependent; 0.5–0.6 is typical.
    let cell_aspect: f32 = 0.55;

    let src_w = img.width() as f32;
    let src_h = img.height() as f32;

    // Fit by width, but also cap height.
    let mut out_w = max_w as u32;
    let mut out_h = ((out_w as f32) * (src_h / src_w) * cell_aspect).round().max(1.0) as u32;

    if out_h > max_h as u32 {
        out_h = max_h as u32;
        out_w = (((out_h as f32) / cell_aspect) * (src_w / src_h)).round().max(1.0) as u32;
        out_w = out_w.min(max_w as u32);
    }

    let resized = image::imageops::resize(&img, out_w, out_h, image::imageops::FilterType::Triangle);

    let mut s = String::new();
    for y in 0..resized.height() {
        for x in 0..resized.width() {
            let p = resized.get_pixel(x, y);
            let a = p[3] as f32 / 255.0;
            if a <= 0.02 {
                s.push(' ');
                continue;
            }
            // The PNG is basically white-on-transparent; use luminance * alpha.
            let r = p[0] as f32 / 255.0;
            let g = p[1] as f32 / 255.0;
            let b = p[2] as f32 / 255.0;
            let lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) * a;
            let idx = (lum * ((ramp.len() - 1) as f32)).round() as usize;
            s.push(ramp[idx.min(ramp.len() - 1)]);
        }
        s.push('\n');
    }
    s
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Screen {
    Welcome,
    Unreal,
    Prereqs,
    AirSim,
    Summary,
    Quit,
}

struct Requirement {
    label: String,
    packages: Vec<String>,
    selected: bool,
}

struct App {
    screen: Screen,
    status: String,
    target: String,
    dry_run: bool,

    // Unreal Engine detection/confirmation
    ue_path: String,
    ue_editing: bool,
    ue_confirmed: bool,
    ue_last_check: String,

    // Prereqs UI
    reqs: Vec<Requirement>,
    req_cursor: usize,
    req_confirmed: bool,
}

fn default_requirements() -> Vec<Requirement> {
    // Minimal-by-default .deb; these are what the TUI will offer to install via apt.
    // Keep labels human-friendly; packages are the apt package names.
    vec![
        Requirement {
            label: "Build essentials (compiler, make, etc.)".into(),
            packages: vec!["build-essential".into()],
            selected: true,
        },
        Requirement {
            label: "Git".into(),
            packages: vec!["git".into()],
            selected: true,
        },
        Requirement {
            label: "CMake".into(),
            packages: vec!["cmake".into()],
            selected: true,
        },
        Requirement {
            label: "Clang".into(),
            packages: vec!["clang".into()],
            selected: true,
        },
        Requirement {
            label: "Python 3".into(),
            packages: vec!["python3".into(), "python3-pip".into()],
            selected: true,
        },
        Requirement {
            label: "Unzip + wget + curl".into(),
            packages: vec!["unzip".into(), "wget".into(), "curl".into()],
            selected: true,
        },
        Requirement {
            label: "Linux headers".into(),
            packages: vec!["linux-headers-generic".into()],
            selected: true,
        },
        Requirement {
            label: "Vulkan tools".into(),
            packages: vec!["vulkan-tools".into()],
            selected: true,
        },
        Requirement {
            label: "X11 / dev libs (UE/AirSim tooling)".into(),
            packages: vec![
                "libxi-dev".into(),
                "libxinerama-dev".into(),
                "libxrandr-dev".into(),
                "libxcursor-dev".into(),
                "libxss-dev".into(),
                "libgl1-mesa-dev".into(),
            ],
            selected: true,
        },
    ]
}

fn selected_packages(app: &App) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    for r in &app.reqs {
        if r.selected {
            out.extend(r.packages.iter().cloned());
        }
    }
    out.sort();
    out.dedup();
    out
}

fn main() -> Result<()> {
    let args = Args::parse();

    let mut terminal = setup_terminal()?;
    let res = run_app(&mut terminal, args);
    restore_terminal(&mut terminal)?;
    res
}

fn setup_terminal() -> Result<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode().context("enable_raw_mode")?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen).context("EnterAlternateScreen")?;
    let backend = CrosstermBackend::new(stdout);
    Terminal::new(backend).context("create terminal")
}

fn restore_terminal(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> Result<()> {
    disable_raw_mode().context("disable_raw_mode")?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen).context("LeaveAlternateScreen")?;
    terminal.show_cursor().context("show_cursor")?;
    Ok(())
}

fn run_app(terminal: &mut Terminal<CrosstermBackend<Stdout>>, args: Args) -> Result<()> {
    let mut reqs = default_requirements();
    let auto_select_all = reqs.len() > 8;
    if auto_select_all {
        for r in &mut reqs {
            r.selected = true;
        }
    }

    let mut app = App {
        screen: Screen::Welcome,
        status: "Press → to continue, ← to go back, q to quit.".into(),
        target: args.target,
        dry_run: args.dry_run,

        ue_path: "~/projects/Sim/UnrealEngine".into(),
        ue_editing: false,
        ue_confirmed: false,
        ue_last_check: "Not checked yet".into(),

        reqs,
        req_cursor: 0,
        req_confirmed: false,
    };

    app.status = status_for(&app);

    loop {
        terminal.draw(|f| ui(f, &app))?;

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }
                // If we are editing the UE path, treat most keys as text input.
                if app.screen == Screen::Unreal && app.ue_editing {
                    match key.code {
                        KeyCode::Esc => {
                            app.ue_editing = false;
                            app.status = status_for(&app);
                        }
                        KeyCode::Enter => {
                            app.ue_editing = false;
                            app.status = status_for(&app);
                        }
                        KeyCode::Backspace => {
                            app.ue_path.pop();
                        }
                        KeyCode::Char(c) => {
                            // Basic filter; allow common path chars.
                            if !c.is_control() {
                                app.ue_path.push(c);
                            }
                        }
                        _ => {}
                    }
                    continue;
                }

                match key.code {
                    KeyCode::Char('q') | KeyCode::Esc => return Ok(()),

                    // Navigation
                    KeyCode::Right => {
                        app.screen = next_screen(app.screen);
                        app.status = status_for(&app);
                        if app.screen == Screen::Quit {
                            return Ok(());
                        }
                    }
                    KeyCode::Left => {
                        app.screen = prev_screen(app.screen);
                        app.status = status_for(&app);
                    }

                    // Enter is overloaded: on prereqs it means "confirm", elsewhere it acts like next.
                    KeyCode::Enter => {
                        if app.screen == Screen::Prereqs {
                            app.reqs.iter().for_each(|_| {});
                            let any = app.reqs.iter().any(|r| r.selected);
                            if any {
                                app.req_confirmed = true;
                                app.status = "Prereqs confirmed. Press → to continue.".into();
                            } else {
                                app.status = "Select at least one item (Space) or auto-select (if >8).".into();
                            }
                        } else {
                            app.screen = next_screen(app.screen);
                            app.status = status_for(&app);
                            if app.screen == Screen::Quit {
                                return Ok(());
                            }
                        }
                    }

                    // Actions
                    KeyCode::Char('r') => {
                        app.status = "(not yet) Run actions for this page".into();
                    }

                    // Unreal page keys
                    KeyCode::Char('e') => {
                        if app.screen == Screen::Unreal {
                            app.ue_editing = true;
                            app.status = "Editing UE path… (Enter to finish, Esc to cancel)".into();
                        }
                    }
                    KeyCode::Char('c') => {
                        if app.screen == Screen::Unreal {
                            match check_unreal_path(&app.ue_path) {
                                Ok(()) => {
                                    app.ue_confirmed = true;
                                    app.ue_last_check = "OK: Unreal Engine looks installed".into();
                                    app.status = "UE confirmed. Press → to continue.".into();
                                }
                                Err(e) => {
                                    app.ue_confirmed = false;
                                    app.ue_last_check = format!("ERROR: {e:#}");
                                    app.status = "UE not detected at that path. Press e to edit path.".into();
                                }
                            }
                        }
                    }

                    // Prereqs selection keys
                    KeyCode::Char(' ') => {
                        if app.screen == Screen::Prereqs {
                            if app.reqs.len() <= 8 {
                                if let Some(r) = app.reqs.get_mut(app.req_cursor) {
                                    r.selected = !r.selected;
                                }
                                app.req_confirmed = false;
                                app.status = status_for(&app);
                            }
                        }
                    }
                    KeyCode::Up => {
                        if app.screen == Screen::Prereqs {
                            app.req_cursor = app.req_cursor.saturating_sub(1);
                            app.status = status_for(&app);
                        }
                    }
                    KeyCode::Down => {
                        if app.screen == Screen::Prereqs {
                            app.req_cursor = (app.req_cursor + 1).min(app.reqs.len().saturating_sub(1));
                            app.status = status_for(&app);
                        }
                    }

                    _ => {}
                }
            }
        }
    }
}

fn next_screen(s: Screen) -> Screen {
    use Screen::*;
    match s {
        Welcome => Unreal,
        Unreal => Prereqs,
        Prereqs => AirSim,
        AirSim => Summary,
        Summary => Quit,
        Quit => Quit,
    }
}

fn prev_screen(s: Screen) -> Screen {
    use Screen::*;
    match s {
        Welcome => Welcome,
        Unreal => Welcome,
        Prereqs => Unreal,
        AirSim => Prereqs,
        Summary => AirSim,
        Quit => Summary,
    }
}

fn status_for(app: &App) -> String {
    match app.screen {
        Screen::Welcome => "→ continue | q quit".into(),
        Screen::Unreal => "e edit path | c confirm/check | ← back | → next | q quit".into(),
        Screen::Prereqs => {
            if app.reqs.len() <= 8 {
                "↑/↓ move | Space toggle | Enter confirm | ← back | → next | q quit".into()
            } else {
                "(auto-selected) ↑/↓ scroll | Enter confirm | ← back | → next | q quit".into()
            }
        }
        _ => "← back | → next | r run (coming soon) | q quit".into(),
    }
}

fn ui(f: &mut Frame, app: &App) {
    let root = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(0), Constraint::Length(3)])
        .split(f.size());

    let block = Block::default()
        .borders(Borders::ALL)
        .title(Title::from(format!(
            " Omnipotent Analytics - TUI Installer for: {} (HIL/SIL Enabled) ",
            app.target
        )))
        .border_style(Style::default().fg(Color::Cyan));

    // Welcome screen gets a special layout (image logo + copy).
    if app.screen == Screen::Welcome {
        let inner = block.inner(root[0]);
        let logo_w = inner.width.saturating_sub(2).min(72);
        let logo_h = inner.height.saturating_sub(6).min(28);

        let logo = render_logo_ascii(logo_w, logo_h);
        let mut copy = String::new();
        copy.push_str("Omnipotent Analytics\n");
        copy.push_str("TUI Installer\n");
        copy.push_str(&format!("Target: {}\n", app.target));
        copy.push_str("(HIL/SIL Enabled)\n\n");
        copy.push_str("→ continue | q quit\n");

        let text = format!("{logo}\n{copy}");

        let paragraph = Paragraph::new(Text::from(text))
            .block(block)
            .style(Style::default().fg(Color::Gray))
            .wrap(Wrap { trim: false });

        f.render_widget(paragraph, root[0]);
    } else {
        let content = match app.screen {
            Screen::Welcome => Text::from(""),
            Screen::Unreal => render_unreal(app),
            Screen::Prereqs => render_prereqs(app),
            Screen::AirSim => render_airsim(app),
            Screen::Summary => render_summary(app),
            Screen::Quit => "Bye".into(),
        };

        let paragraph = Paragraph::new(content)
            .block(block)
            .wrap(Wrap { trim: false });

        f.render_widget(paragraph, root[0]);
    }

    let status = Paragraph::new(app.status.clone())
        .block(Block::default().borders(Borders::ALL).title(" Status "));
    f.render_widget(status, root[1]);
}

fn render_prereqs(app: &App) -> Text<'static> {
    let guide_url = "https://microsoft.github.io/AirSim/build_linux/";
    let guide_link = format!(
        "\u{1b}]8;;{guide_url}\u{1b}\\{guide_url}\u{1b}]8;;\u{1b}\\"
    );

    let mut s = String::new();
    s.push_str("Prerequisites (Pop!_OS + NVIDIA GPU)\n\n");
    s.push_str("Baseline guide: ");
    s.push_str(&guide_link);
    s.push_str("\n\n");

    let auto = app.reqs.len() > 8;
    if auto {
        s.push_str("This install has a long dependency list; everything is pre-selected.\n");
        s.push_str("Scroll to review what will be installed, then press Enter to confirm.\n\n");
    } else {
        s.push_str("Select what to install (Space toggles). Then press Enter to confirm.\n\n");
    }

    s.push_str("Items:\n");

    // Render a scroll window of requirements.
    // We do it as plain text for now; later we can switch to a proper ratatui List widget.
    let view_h: usize = 12; // "scroll window" size
    let start = if app.req_cursor >= view_h {
        app.req_cursor + 1 - view_h
    } else {
        0
    };
    let end = (start + view_h).min(app.reqs.len());

    for (i, r) in app.reqs.iter().enumerate().take(end).skip(start) {
        let cursor = if i == app.req_cursor { ">" } else { " " };
        let mark = if r.selected { "[x]" } else { "[ ]" };
        s.push_str(&format!("{cursor} {mark} {}\n", r.label));
    }

    if end < app.reqs.len() {
        s.push_str(&format!("… ({} more)\n", app.reqs.len() - end));
    }

    s.push_str("\nPackages that will be installed (flattened):\n");
    let pkgs = selected_packages(app);
    if pkgs.is_empty() {
        s.push_str("(none selected)\n");
    } else {
        // Keep it readable: wrap-ish with newlines.
        let mut line = String::new();
        for p in pkgs {
            if line.len() + p.len() + 1 > 80 {
                s.push_str(&line);
                s.push('\n');
                line.clear();
            }
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(&p);
        }
        if !line.is_empty() {
            s.push_str(&line);
            s.push('\n');
        }
    }

    s.push_str("\n");
    if app.req_confirmed {
        s.push_str("Confirmed ✅\n");
    } else {
        s.push_str("Not confirmed yet.\n");
    }

    if app.dry_run {
        s.push_str("\nDRY-RUN is ON: we will not execute commands that change your system.\n");
    }

    Text::from(s)
}

fn render_airsim(app: &App) -> Text<'static> {
    let mut s = String::new();
    s.push_str("AirSim steps (automatable parts):\n\n");
    s.push_str("1) Clone AirSim\n");
    s.push_str("2) Run setup.sh (downloads dependencies)\n");
    s.push_str("3) Run build.sh\n\n");
    s.push_str("We will provide a guided, reproducible directory layout and logging.\n\n");

    if !app.ue_confirmed {
        s.push_str("NOTE: Unreal Engine is not confirmed yet. Go back and confirm UE before continuing.\n\n");
    }
    if !app.req_confirmed {
        s.push_str("NOTE: Prereqs are not confirmed yet. Go back and confirm prereqs before continuing.\n\n");
    }

    s.push_str("Press r (coming soon) to execute these steps.\n");
    Text::from(s)
}

fn render_unreal(app: &App) -> Text<'static> {
    // OSC 8 hyperlinks (supported by many terminals). If unsupported, it will just show text.
    let epic_url = "https://www.unrealengine.com/";
    let guide_url = "https://microsoft.github.io/AirSim/build_linux/";
    let epic_link = format!(
        "\u{1b}]8;;{epic_url}\u{1b}\\{epic_url}\u{1b}]8;;\u{1b}\\"
    );
    let guide_link = format!(
        "\u{1b}]8;;{guide_url}\u{1b}\\{guide_url}\u{1b}]8;;\u{1b}\\"
    );

    let mut s = String::new();
    s.push_str("Build Unreal Engine (FIRST)\n\n");
    s.push_str("Make sure you are registered with Epic Games. This is required to get source code access for Unreal Engine.\n");
    s.push_str("Register/login: ");
    s.push_str(&epic_link);
    s.push_str("\n\n");
    s.push_str("Note: We only support Unreal >= 4.27 at present. We recommend using 4.27.\n\n");
    s.push_str("We recommend building in: ~/projects/Sim/\n\n");
    s.push_str("Commands (from the AirSim guide): ");
    s.push_str(&guide_link);
    s.push_str("\n\n");
    s.push_str("# go to the folder where you clone GitHub projects\n");
    s.push_str("git clone -b 4.27 git@github.com:EpicGames/UnrealEngine.git\n");
    s.push_str("cd UnrealEngine\n");
    s.push_str("./Setup.sh\n");
    s.push_str("./GenerateProjectFiles.sh\n");
    s.push_str("make\n\n");

    s.push_str("When done, confirm where it is installed:\n");
    s.push_str(&format!("UE path: {}\n", app.ue_path));
    s.push_str(&format!("Last check: {}\n\n", app.ue_last_check));

    s.push_str("Keys:\n");
    s.push_str("- e : edit UE path\n");
    s.push_str("- c : check/confirm UE install at path\n");
    s.push_str("- → : continue\n");

    if app.ue_confirmed {
        s.push_str("\nUE confirmed ✅\n");
    } else {
        s.push_str("\nUE not confirmed yet.\n");
    }

    Text::from(s)
}

fn render_summary(_app: &App) -> Text<'static> {
    Text::from(
        "Summary:\n\n\
This will become a guided TUI installer that:\n\
- Starts with Unreal Engine 4.27 source build instructions + path confirmation\n\
- Validates Pop!_OS + NVIDIA stack\n\
- Installs dependencies\n\
- Clones/builds AirSim per official docs\n\n\
Next: wire up real prerequisite checks + command execution, then package as a .deb.\n",
    )
}

fn expand_tilde(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{home}/{rest}");
        }
    }
    path.to_string()
}

fn check_unreal_path(path: &str) -> Result<()> {
    let p = expand_tilde(path);
    let p = std::path::Path::new(&p);

    if !p.exists() {
        return Err(anyhow!("path does not exist: {}", p.display()));
    }

    // Common markers for UE4 source build.
    let setup = p.join("Setup.sh");
    let gen = p.join("GenerateProjectFiles.sh");
    let engine_dir = p.join("Engine");

    if !setup.exists() {
        return Err(anyhow!("missing Setup.sh in {}", p.display()));
    }
    if !gen.exists() {
        return Err(anyhow!("missing GenerateProjectFiles.sh in {}", p.display()));
    }
    if !engine_dir.exists() {
        return Err(anyhow!("missing Engine/ in {}", p.display()));
    }

    // Optional: check for built editor binary (may differ depending on build/config).
    let editor = p.join("Engine/Binaries/Linux/UE4Editor");
    if !editor.exists() {
        return Err(anyhow!(
            "Unreal appears cloned but not built yet (expected {} to exist). Run `make` and try again.",
            editor.display()
        ));
    }

    Ok(())
}

#[allow(dead_code)]
async fn run_cmd(cmd: &str, args: &[&str]) -> Result<()> {
    let status = tokio::process::Command::new(cmd)
        .args(args)
        .status()
        .await
        .with_context(|| format!("failed to execute {cmd}"))?;
    if !status.success() {
        return Err(anyhow!("command failed: {cmd} {args:?}"));
    }
    Ok(())
}
