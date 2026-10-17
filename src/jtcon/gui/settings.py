"""File › Settings: the config file's settings, a page per subject.

The fields come from the config's own list of settings (config._SCHEMA): a
checkbox for true/false, a drop-down for a choice, and so on.  Each shows the
file's value, else the default.  Save writes only what changed (ConfigFile:
the file's comments and layout stay), after the same checks as at startup,
then reloads.  A setting the command line or a profile sets this run is
marked: theirs wins.

On a profile (Profile › settings), each setting has a "Set here" box:
unticked, it's the config file's value, greyed; ticked, the profile sets it.
The Wanted rules page has one for the whole list, which a profile's rules
replace."""

import html
import re
import tomllib

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from jtcon.config import (_CALL, _COLOR, _PATH, _SCHEMA, Config, ConfigError, _load_layer,
                          profile_path)
from jtcon.config_edit import ConfigFile
from jtcon.core.messages import _CALLSIGN_RE
from jtcon.gui.wanted_page import WantedPage

# (label, help) for every setting.  The pages list them in order.
FIELDS = {
    ("udp", "port"): ("UDP port", "WSJT-X's Settings › Reporting › UDP Server port."),
    ("udp", "host"): ("Listen on", "The address to listen on: 0.0.0.0 for every network,"
                      " 127.0.0.1 for this computer only."),
    ("udp", "proxy"): ("Forward to ports", "Pass every packet on to these ports on this"
                       " computer, for other programs (GridTracker…).  Separate them with"
                       " commas."),
    ("display", "ui"): ("Screen", "What jtcon starts with: the classic screen, the Textual"
                        " one, or this window (gui).  Used at the next start."),
    ("display", "theme"): ("Theme", "This window: dark, light, or system for your desktop's"
                           " own look."),
    ("display", "layout"): ("First layout", "The panels' layout the first time (after that"
                            " the window remembers its own); auto picks by the screen's"
                            " size."),
    ("display", "show"): ("Show", "cq: CQs and wanted stations; all: every decode."),
    ("display", "wanted_timeout_minutes"): ("Wanted timeout (minutes)", "A station unheard"
                                            " this long leaves the Wanted table."),
    ("display", "show_time"): ("Show the time", "Classic screen: the local time before each"
                               " line."),
    ("display", "color"): ("Colours", "Classic and Textual screens: colours in the decodes."),
    ("display", "mouse"): ("Mouse", "Classic screen: the mouse wheel scrolls; hold Shift to"
                           " select text."),
    ("display", "debug"): ("Debug", "Show the raw packets."),
    ("scope", "decodes"): ("Check", "cq: CQ decodes only; all: every decode (WSJT-X's"
                           " \"CQ only\" must be off)."),
    ("filters", "suppress_worked_within_days"): (
        "Worked within (days)", "A station worked this recently isn't wanted again for what"
        " that QSO covers (a 20m QSO doesn't hide 5BWAS on 40m), nor for any watchlist."
        "  0: worked today only; -1: never."),
    ("filters", "ignore"): ("Ignore", "Calls never shown or alerted on, unless they call you"
                            " (W1ABC covers W1ABC/P too).  Separate them with spaces or"
                            " commas."),
    ("inputs", "adif"): ("ADIF logs", "Log files besides WSJT-X's own, which is found by"
                         " itself.  One per line."),
    ("inputs", "cty"): ("cty.dat", "The country file; downloaded again when it's older than"
                        " 30 days."),
    ("inputs", "hamdat"): ("hamdat database", "The FCC database, for US states."),
    ("inputs", "qrz", "enabled"): ("Use my QRZ logbook", "Confirmations from your QRZ"
                                   " logbook, and a daily backup of it."),
    ("inputs", "qrz", "api_key"): ("API key", "From your QRZ logbook's settings.  Empty:"
                                   " the QRZ_API_KEY environment variable, else qrzlog's"
                                   " qrzlog.ini."),
    ("actions", "qrz", "upload"): ("Upload QSOs", "Send each QSO WSJT-X logs to your QRZ"
                                   " logbook (with the API key above)."),
    ("inputs", "qrz", "dir"): ("Folder", "Where the downloads and backups are kept."),
    ("inputs", "qrz", "keep"): ("Downloads kept", "Full downloads kept, one a day."),
    ("inputs", "qrz", "refresh_minutes"): ("Refresh (minutes)", "How often to fetch what"
                                           " changed in the logbook."),
    ("actions", "highlight", "enabled"): ("Highlight in WSJT-X", "Colour wanted stations in"
                                          " WSJT-X's Band Activity window."),
    ("actions", "highlight", "background"): ("Background", "The highlight's background."),
    ("actions", "highlight", "foreground"): ("Text", "The highlight's text colour."),
    ("actions", "ntfy", "topic"): ("ntfy topic", "Push wanted alerts to ntfy.sh/<topic>."),
    ("actions", "script", "on_wanted"): ("Script: wanted", "Run for each wanted alert, with"
                                         " the alert's text."),
    ("actions", "script", "on_log_reload"): ("Script: log reloaded", "Run when a log file"
                                             " changes, with the ADIF files' paths."),
    ("actions", "json_log", "file"): ("JSON log", "Append each CQ to this file as a line of"
                                      " JSON."),
}

PAGES = [
    ("WSJT-X", [("udp", "port"), ("udp", "host"), ("udp", "proxy")]),
    ("Display", [("display", k) for k in ("ui", "theme", "layout", "show",
                                          "wanted_timeout_minutes", "show_time", "color",
                                          "mouse", "debug")]),
    ("Checking", [("scope", "decodes"), ("filters", "suppress_worked_within_days"),
                  ("filters", "ignore")]),
    ("Logs and data", [("inputs", "adif"), ("inputs", "cty"), ("inputs", "hamdat")]),
    ("QRZ logbook", [("inputs", "qrz", "enabled"), ("inputs", "qrz", "api_key"),
                     ("actions", "qrz", "upload"), ("inputs", "qrz", "dir"),
                     ("inputs", "qrz", "keep"), ("inputs", "qrz", "refresh_minutes")]),
    ("Actions", [("actions", "highlight", "enabled"), ("actions", "highlight", "background"),
                 ("actions", "highlight", "foreground"), ("actions", "ntfy", "topic"),
                 ("actions", "script", "on_wanted"), ("actions", "script", "on_log_reload"),
                 ("actions", "json_log", "file")]),
]

_FOLDERS = {("inputs", "qrz", "dir")}
_NEW_FILES = {("inputs", "cty"), ("actions", "json_log", "file")}   # may not exist yet


def kind_of(keys: tuple):
    """The setting's kind in config._SCHEMA: bool, int, str, a choice, _PATH…"""
    node = _SCHEMA
    for key in keys:
        node = node[key]
    return node


def schema_keys(node=_SCHEMA, prefix=()) -> list:
    """Every setting's keys."""
    out = []
    for key, kind in node.items():
        if isinstance(kind, dict):
            out += schema_keys(kind, prefix + (key,))
        else:
            out.append(prefix + (key,))
    return out


def _lookup(d: dict, keys: tuple):
    for key in keys:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d


DEFAULTS = Config().as_dict()


def default_of(keys: tuple):
    return _lookup(DEFAULTS, keys)


class FieldError(Exception):
    pass


class Field:
    """One setting's row: its widget, the value shown, and what it was."""

    here = None                 # on a profile: its Set here box

    def __init__(self, keys: tuple, value):
        self.keys = keys
        self.kind = kind_of(keys)
        self.label, self.help = FIELDS[keys]
        self.widget = self._make()
        self.set(value)
        self.initial = self.value()

    @property
    def name(self) -> str:
        return ".".join(self.keys)

    def _make(self) -> QWidget:
        kind = self.kind
        if kind is bool:
            self.edit = QCheckBox()
            return self.edit
        if isinstance(kind, tuple):
            self.edit = QComboBox()
            self.edit.addItems(list(kind[1]))
            return self.edit
        if kind is int:
            self.edit = QSpinBox()
            self.edit.setRange(1, 65535) if self.keys == ("udp", "port") else \
                self.edit.setRange(-999_999, 999_999)
            return self.edit
        if kind is _COLOR:
            self.edit = QPushButton()
            self.edit.clicked.connect(self._pick_colour)
            return self.edit
        if kind == [_PATH]:
            self.edit = QPlainTextEdit()
            self.edit.setFixedHeight(self.edit.fontMetrics().height() * 4 + 12)
            return self._with_button("Add file…", self._add_file)
        self.edit = QLineEdit()
        if kind is _PATH:
            return self._with_button("Browse…", self._browse)
        return self.edit

    def _with_button(self, text: str, slot) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit)
        button = QPushButton(text)
        button.clicked.connect(slot)
        row.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)
        return box

    # ── Values ────────────────────────────────────────────────────────────────

    def set(self, value) -> None:
        kind, edit = self.kind, self.edit
        if kind is bool:
            edit.setChecked(bool(value))
        elif isinstance(kind, tuple):
            edit.setCurrentText(str(value))
        elif kind is int:
            edit.setValue(int(value or 0))
        elif kind is _COLOR:
            self.colour = str(value or "#000000").lower()
            edit.setText(self.colour)
            edit.setStyleSheet(f"background: {self.colour}; color: "
                               f"{'black' if QColor(self.colour).lightness() > 128 else 'white'};")
        elif isinstance(kind, list):
            items = [str(v) for v in (value if isinstance(value, list) else
                                      [value] if value is not None else [])]
            if kind == [_PATH]:
                edit.setPlainText("\n".join(items))
            else:
                edit.setText(", ".join(items))
        else:
            edit.setText("" if value is None else str(value))

    def value(self):
        """The value as the file would have it; None for an empty text box."""
        kind, edit = self.kind, self.edit
        if kind is bool:
            return edit.isChecked()
        if isinstance(kind, tuple):
            return edit.currentText()
        if kind is int:
            return edit.value()
        if kind is _COLOR:
            return self.colour
        if kind == [_PATH]:
            return [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]
        if isinstance(kind, list):
            words = edit.text().replace(",", " ").split()
            if kind == [int]:
                bad = [w for w in words if not w.isdigit()]
                if bad:
                    raise FieldError(f"{self.label}: {bad[0]!r} isn't a port number")
                return [int(w) for w in words]
            calls = [w.upper() for w in words]
            bad = [c for c in calls if kind == [_CALL] and not _CALLSIGN_RE.match(c)]
            if bad:
                raise FieldError(f"{self.label}: {bad[0]} isn't a callsign")
            return calls
        return edit.text().strip() or None

    @property
    def changed(self) -> bool:
        return self.value() != self.initial

    # ── Pickers ───────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        start = self.edit.text() or str(default_of(self.keys) or "")
        if self.keys in _FOLDERS:
            path = QFileDialog.getExistingDirectory(self.widget, self.label, start)
        elif self.keys in _NEW_FILES:
            path, _ = QFileDialog.getSaveFileName(
                self.widget, self.label, start,
                options=QFileDialog.Option.DontConfirmOverwrite)
        else:
            path, _ = QFileDialog.getOpenFileName(self.widget, self.label, start)
        if path:
            self.edit.setText(path)

    def _add_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self.widget, self.label, "",
                                                "ADIF logs (*.adi *.adif);;All files (*)")
        if paths:
            self.set(self.value() + paths)

    def _pick_colour(self) -> None:
        colour = QColorDialog.getColor(QColor(self.colour), self.widget, self.label)
        if colour.isValid():
            self.set(colour.name())


def _wrapping(text: str) -> QLabel:
    """A label that wraps across the whole field column, never inside an
    option such as --port."""
    label = QLabel(re.sub(r"(--\S+(?: \S+)?)", r"<nobr>\1</nobr>", html.escape(text)))
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    # Qt reserves a wrapped label's height for a narrow guess at its width;
    # a realistic minimum keeps that close, with no gaps between the settings.
    label.setMinimumWidth(380)
    return label


class SettingsDialog(QDialog):
    """The settings of *window*'s config file (window.startup.file), or of
    its *profile*."""

    def __init__(self, window, profile: str | None = None):
        super().__init__(window)
        self.window = window
        self.startup = window.startup
        self.profile = profile
        self.main_path = self.startup.file
        self.path = profile_path(profile, self.main_path) if profile else self.main_path
        self.setWindowTitle(f"Settings — profile {profile} — {self.path}" if profile
                            else f"Settings — {self.path}")
        self.load_error = None
        file = self.main = None
        try:
            file = ConfigFile(self.path)
            if profile:
                self.main = ConfigFile(self.main_path)  # what's not set here
        except ConfigError as exc:
            file, self.load_error = None, str(exc)
        self.profile_sets = set() if profile else self._profile_settings()
        self.fields: dict = {}
        self.page_of: dict = {}             # setting -> its page's row
        self.pages = QListWidget()
        self.stack = QStackedWidget()
        self.file_wanted = file.wanted() if file is not None else None
        if profile:
            self.wanted_page = WantedPage(
                self.file_wanted, self.notes(("wanted",)),
                base=self.main.wanted() if self.main is not None else None,
                base_name=self.main_path.name)
        else:
            self.wanted_page = WantedPage(self.file_wanted, self.notes(("wanted",)))
        for title, keys in PAGES:
            if title == "Checking":         # the rules first: what jtcon looks for
                self.wanted_row = self.pages.count()
                self.pages.addItem("Wanted rules")
                self.stack.addWidget(self.wanted_page)
            self.page_of.update({k: self.pages.count() for k in keys})
            self.pages.addItem(title)
            self.stack.addWidget(self._page(keys, file))
        self.pages.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.pages.setCurrentRow(0)
        self.pages.setFixedWidth(self.pages.sizeHintForColumn(0) + 24)
        body = QHBoxLayout()
        body.addWidget(self.pages)
        body.addWidget(self.stack, 1)
        self.message = QLabel(self.load_error or "")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #e5484d;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("&Save and use" if profile and profile != self.startup.profile
                                 else "&Save and reload")
        self.save_button.setEnabled(file is not None)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        note = QLabel(self._note())
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addLayout(body, 1)
        layout.addWidget(self.message)
        layout.addWidget(buttons)
        self.resize(820, 600)

    def _note(self) -> str:
        if self.profile:
            main = self.main_path.name
            return (f"Profile {self.profile} ({self.path}): layered on top of {main}.  Tick"
                    f" Set here for each setting this profile changes; the others are {main}'s"
                    " (greyed).  Save writes only what you change, keeps a dated copy of the"
                    f" file as it was, and restarts with profile {self.profile}.")
        text = (f"The settings in {self.path}, used when jtcon starts.  Save writes only"
                " what you change (the file's comments stay), keeps a dated copy of the"
                " file as it was, and reloads.")
        if self.startup.profile:
            text += (f"  Profile {self.startup.profile} is in use: the settings it sets"
                     " win over these (marked).")
        return text

    def _profile_settings(self) -> set:
        """The settings the profile in use sets (they win over the file's)."""
        path = self.startup.profile_file
        if path is None:
            return set()
        try:
            with open(path, "rb") as f:
                layer = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return set()
        return {keys for keys in [*FIELDS, ("wanted",)] if _lookup(layer, keys) is not None}

    def _page(self, keys_list: list, file) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        # Each label beside its setting, at the top of the setting's block.
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        outer.addLayout(form)
        outer.addStretch(1)                     # room to spare goes below, not between
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for keys in keys_list:
            value = file.get(keys) if file is not None else None
            here = value is not None
            inherited = self.main.get(keys) if self.main is not None else None
            if inherited is None:
                inherited = default_of(keys)
            if value is None:
                value = inherited
            field = self.fields[keys] = Field(keys, value)
            field.widget.setToolTip(field.help)
            default = default_of(keys)
            shown = (", ".join(map(str, default)) if isinstance(default, list)
                     else default)
            text = field.help
            if shown not in (None, "", []) and field.kind is not bool:
                text += f"  Default: {shown}."
            # The setting, its explanation and any marks, one block in the
            # form's field column (wrapped labels lay out badly on rows of their own).
            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(0, 0, 0, 8)
            column.setSpacing(2)
            short = (field.kind is bool or isinstance(field.kind, tuple)
                     or field.kind in (int, _COLOR))
            if self.profile:
                field.here = QCheckBox("Set here")
                field.here.setToolTip(f"Ticked: this profile sets it.  Unticked:"
                                      f" {self.main_path.name}'s value (shown greyed).")
                field.here.setChecked(here)
                field.here_initial, field.inherited = here, inherited
                field.widget.setEnabled(here)
                field.here.toggled.connect(lambda on, f=field: self._set_here(f, on))
            if short or self.profile:
                row = QHBoxLayout()             # its own width, at the left
                if self.profile:
                    row.addWidget(field.here, alignment=Qt.AlignmentFlag.AlignTop)
                row.addWidget(field.widget, 0 if short else 1)
                if short:
                    row.addStretch()
                column.addLayout(row)
            else:
                column.addWidget(field.widget)
            help_label = _wrapping(text)
            help_label.setEnabled(False)            # greyed: it's an explanation
            column.addWidget(help_label)
            for note in self.notes(keys):
                mark = _wrapping(f"⚑ {note}")
                mark.setStyleSheet("color: #e5a50a;")
                column.addWidget(mark)
            column.addStretch(1)
            form.addRow(field.label, cell)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _set_here(self, field, on: bool) -> None:
        field.widget.setEnabled(on)
        if not on:
            field.set(field.inherited)      # what's used without it

    def notes(self, keys: tuple) -> list:
        """Why the setting saved here isn't what's in use this run."""
        out = []
        flag = self.startup.flags.get(keys)
        if flag:
            if keys == ("filters", "ignore"):
                out.append(f"The command line adds to this list this run: {flag}.")
            else:
                out.append(f"The command line's {flag} wins this run.")
        if keys == ("wanted",):
            if flag:
                out[-1] = (f"The command line's {flag} wins this run: these rules are"
                           " used when jtcon starts without it.")
            if keys in self.profile_sets:
                out.append(f"Profile {self.startup.profile} has its own rules, which win"
                           " over these.")
        elif keys in self.profile_sets:
            out.append(f"Profile {self.startup.profile} sets this: its value wins.")
        return out

    # ── Saving ────────────────────────────────────────────────────────────────

    def changes(self) -> dict:
        """{keys: new value, or None to remove it} for the fields changed;
        FieldError if one can't be read."""
        out = {}
        for keys, f in self.fields.items():
            here = getattr(f, "here", None)
            if here is None:                # the config file: empty is the default
                if f.changed:
                    value = f.value()
                    out[keys] = None if value in (None, []) else value
            elif here.isChecked():          # a profile: empty means empty
                if not f.here_initial or f.changed:
                    out[keys] = self._profile_value(f)
            elif f.here_initial:
                out[keys] = None
        return out

    def _profile_value(self, field):
        value = field.value()
        if value is None:
            if field.kind is not str:
                raise FieldError(f"{field.label}: a profile can't set this to nothing; untick"
                                 f" Set here for {self.main_path.name}'s")
            value = ""
        return value

    def save(self) -> None:
        """Write the changes to the file as it is now (it may have been edited
        meanwhile: only these settings change), check, save, reload."""
        try:
            changes = self.changes()
            base = None
            if self.profile:                # checked on top of the config file
                base = _load_layer(self.main_path) if self.main_path.exists() else {}
            file = ConfigFile(self.path, base=base)
            for keys, value in changes.items():
                file.set(keys, value)       # None removes it
            if self.wanted_page.changed:
                if file.wanted() != self.file_wanted:
                    where = f"profile {self.profile}" if self.profile else "the config file"
                    raise ConfigError(f"The rules in {where} changed since this box"
                                      " opened (in an editor?).  Cancel, then open Settings"
                                      " again.")
                if self.wanted_page.own:
                    file.set_wanted(self.wanted_page.result())
                else:
                    file.remove_wanted()
            self._check_rule_files(file)
            backup = file.save()
        except (ConfigError, FieldError) as exc:
            self.show_error(str(exc))
            return
        self.accept()
        log = self.window.log_view.add
        switch = self.profile is not None and self.profile != self.startup.profile
        if backup is False:
            log("[config] Settings: nothing changed.")
            if switch:
                self.window.use_profile(self.profile)
            return
        log(f"[config] Saved {self.path}" + (f" (the old one: {backup.name})" if backup else ""))
        if switch:
            self.window.use_profile(self.profile)
        else:
            self.window.reload_config()

    def _check_rule_files(self, file) -> None:
        """The rules' files must be there: jtcon can't start without them."""
        for spec in file.check().wanted:
            settings = spec.settings
            paths = list(settings.get("files") or []) + (
                [settings["file"]] if settings.get("file") else [])
            for path in paths:
                if not path.exists():
                    raise ConfigError(f"Wanted rule {spec.name}: {path}: no such file")

    def show_error(self, text: str) -> None:
        """Show *text*, and the page of the setting it's about."""
        self.message.setText(text)
        # Longest names first: display.show_time before display.show.
        for keys, field in sorted(self.fields.items(), key=lambda kf: -len(kf[1].name)):
            if field.name in text or text.startswith(field.label + ":"):
                self.pages.setCurrentRow(self.page_of[keys])
                field.edit.setFocus()
                return
        if "wanted" in text or "rule" in text:
            self.pages.setCurrentRow(self.wanted_row)
