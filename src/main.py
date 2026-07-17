import os
import sys
import time

# NOTE: torch must be imported before PyQt5 on Windows. If PyQt5 loads first,
# it initializes runtime DLLs that break torch's c10.dll load
# (OSError [WinError 1114] "DLL initialization routine failed").
# Importing torch here first loads its DLLs cleanly; Qt then coexists.
try:
    import torch  # noqa: F401
except ImportError:
    pass

from audioplayer import AudioPlayer
from pynput.keyboard import Controller, Key
from PyQt5.QtCore import QObject, QProcess
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox, QLineEdit

import clipboard_utils

IS_WINDOWS = sys.platform == 'win32'

# win32clipboard is Windows-only; macOS/Linux use clipboard_utils instead.
if IS_WINDOWS:
    import win32clipboard
    import win32con

from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager
from llm_processor import LLMProcessor


# --- macOS/pynput-Workaround --------------------------------------------------
# pynput löst HIServices.AXIsProcessTrusted() erst im Listener-Hintergrundthread
# auf. pyobjcs Lazy-Import ist dabei nicht thread-sicher und wirft sporadisch
# KeyError('AXIsProcessTrusted') -> der Listener-Thread stirbt und der Hotkey
# funktioniert nie (auch mit erteilten Rechten). Den Aufruf hier einmal im
# Hauptthread "vorwärmen", damit der Wert gecached ist, bevor der Listener startet.
if sys.platform == 'darwin':
    try:
        import HIServices
        HIServices.AXIsProcessTrusted()
    except Exception:
        pass


class QalamApp(QObject):
    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        ConfigManager.initialize()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.restart_app)

        if ConfigManager.config_file_exists():
            self.initialize_components()
            # Start listening immediately instead of showing main window
            self.key_listener.start()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        self.input_simulator = InputSimulator()

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_activate_with_llm", self.on_activation_with_llm_cleanup)
        self.key_listener.add_callback("on_activate_with_llm_instruction", self.on_activation_with_llm_instruction)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)
        self.key_listener.add_callback("on_deactivate_with_llm", self.on_deactivation_with_llm)
        self.key_listener.add_callback("on_deactivate_with_llm_instruction", self.on_deactivation_with_llm_instruction)
        self.key_listener.add_callback("on_text_cleanup", self.handle_text_cleanup)

        model_options = ConfigManager.get_config_section('model_options')
        model_path = model_options.get('local', {}).get('model_path')
        self.local_model = create_local_model() if not model_options.get('use_api') else None

        self.result_thread = None
        self.llm_processor = LLMProcessor() if ConfigManager.get_config_value('llm_post_processing', 'enabled') else None

        self.auto_restart_after_submit = False
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()
            self.status_window.autoSubmitSignal.connect(self.on_auto_submit)

        self.create_tray_icon()

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo.png')), self.app)

        tray_menu = QMenu()

        settings_action = QAction('Settings', self.app)
        settings_action.triggered.connect(self.settings_window.show)
        tray_menu.addAction(settings_action)

        exit_action = QAction('Exit', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def cleanup(self):
        # getattr-Absicherung: Wird beim ersten Start (Settings speichern, bevor
        # initialize_components() lief) über restart_app() aufgerufen – dann gibt es
        # key_listener/input_simulator noch nicht. Betrifft macOS wie Windows.
        if getattr(self, 'key_listener', None):
            self.key_listener.stop()
        if getattr(self, 'input_simulator', None):
            self.input_simulator.cleanup()

    def exit_app(self):
        """
        Exit the application.
        """
        self.cleanup()
        QApplication.quit()

    def restart_app(self):
        """Restart the application to apply the new settings."""
        self.cleanup()
        QApplication.quit()
        QProcess.startDetached(sys.executable, sys.argv)

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not os.path.exists(os.path.join('src', 'config.yaml')):
            QMessageBox.information(
                self.settings_window,
                'Using Default Values',
                'Settings closed without saving. Default values are being used.'
            )
            self.initialize_components()

    def on_activation(self, use_llm=False, is_instruction_mode=False):
        """
        Called when the activation key combination is pressed.
        Args:
            use_llm (bool): Whether to use LLM processing for this activation
            is_instruction_mode (bool): Whether to use instruction mode for LLM processing
        """
        # Standard-Diktat automatisch durch die LLM-Strukturierung
        # (Cleanup-Modus) schicken, wenn llm_post_processing aktiv ist. So wird jedes
        # Diktat strukturiert getippt statt 1:1 – ohne einen extra Hotkey drücken zu müssen.
        if (not use_llm and not is_instruction_mode and self.llm_processor
                and ConfigManager.get_config_value('llm_post_processing', 'enabled')):
            use_llm = True

        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return
        
        # If we get here, no thread is running, so start one
        self.use_llm = use_llm
        self.is_instruction_mode = is_instruction_mode
        self.start_result_thread()

    def on_activation_with_llm_cleanup(self):
        """Activation with LLM cleanup based on recording mode"""
        recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
        # Enable LLM for both press_to_toggle and hold_to_record modes
        # self.use_llm = recording_mode in ('press_to_toggle', 'hold_to_record')
        self.on_activation(use_llm=True, is_instruction_mode=False)

    def on_activation_with_llm_instruction(self):
        """Activation with LLM instruction processing based on recording mode"""
        recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
        # Enable LLM for both press_to_toggle and hold_to_record modes
        # self.use_llm = recording_mode in ('press_to_toggle', 'hold_to_record')
        """Activation with LLM instruction processing"""
        self.on_activation(use_llm=True, is_instruction_mode=True)

    def on_deactivation(self, use_llm=False, is_instruction_mode=False):
        """
        Called when the activation key combination is released.
        Args:
            use_llm (bool): Whether to use LLM processing for this deactivation
            is_instruction_mode (bool): Whether to use instruction mode for LLM processing
        """
        # Set the flags just like in on_activation
        self.use_llm = use_llm
        self.is_instruction_mode = is_instruction_mode
        
        ConfigManager.console_print(f"Deactivation called - use_llm: {use_llm}, is_instruction_mode: {is_instruction_mode}")
        ConfigManager.console_print(f"Recording mode: {ConfigManager.get_config_value('recording_options', 'recording_mode')}")
        ConfigManager.console_print(f"Result thread running: {self.result_thread and self.result_thread.isRunning()}")
        
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                ConfigManager.console_print("Stopping recording...")
                self.result_thread.stop_recording()

    def on_deactivation_with_llm(self):
        """Called when the LLM cleanup activation key combination is released."""
        ConfigManager.console_print("LLM cleanup deactivation triggered")
        self.on_deactivation(use_llm=True, is_instruction_mode=False)

    def on_deactivation_with_llm_instruction(self):
        """Called when the LLM instruction activation key combination is released."""
        ConfigManager.console_print("LLM instruction deactivation triggered")
        self.on_deactivation(use_llm=True, is_instruction_mode=True)

    def start_result_thread(self):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self.result_thread = ResultThread(self.local_model, self.use_llm)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_auto_submit(self):
        """Auto-Submit-Zeit erreicht (Signal aus dem Status-Fenster).
        stop_recording() beendet die Aufnahme regulaer -> es wird transkribiert + getippt;
        danach startet on_transcription_complete die naechste Aufnahme automatisch."""
        if self.result_thread and self.result_thread.isRunning():
            self.auto_restart_after_submit = True
            self.result_thread.stop_recording()

    def on_transcription_complete(self, result):
        """Process transcription with or without LLM based on activation type."""
        try:
            # Temporarily disable key listener
            if self.key_listener:
                self.key_listener.stop()

            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            # LLM-Cleanup an 'enabled' koppeln statt an self.use_llm.
            # Grund: bei press_to_toggle setzt der Key-Release (on_deactivation) self.use_llm
            # zurueck auf False, BEVOR die Transkription fertig ist -> mit self.use_llm liefe
            # der Cleanup nie. Ueber 'enabled' laeuft er zuverlaessig bei jedem Diktat.
            llm_should_run = (self.llm_processor is not None
                              and ConfigManager.get_config_value('llm_post_processing', 'enabled'))
            if llm_should_run and recording_mode in ('press_to_toggle', 'hold_to_record', 'continuous', 'voice_activity_detection'):
                try:
                    # Get the system message based on the mode
                    if self.is_instruction_mode:
                        base_message = ConfigManager.get_config_value("llm_post_processing", "instruction_system_message")
                        file_path = ConfigManager.get_config_value("llm_post_processing", "instruction_system_message_file_path")
                        mode_name = "instruction"
                    else:
                        base_message = ConfigManager.get_config_value("llm_post_processing", "system_prompt")
                        file_path = ConfigManager.get_config_value("llm_post_processing", "system_prompt_file_path")
                        mode_name = "cleanup"
                    
                    # Start with a clean system message
                    system_message = base_message.strip() if base_message else ""
                    
                    ConfigManager.console_print(f"Retrieved {mode_name} base message from settings: {system_message}")
                    
                    if not file_path:
                        ConfigManager.console_print("No file path set, using only the system message from settings")
                    elif not os.path.exists(file_path):
                        ConfigManager.console_print(f"File path set but file not found: {file_path}, using only the system message from settings")
                    
                    # Append file contents if file path exists and is not empty
                    if file_path and os.path.exists(file_path):
                        try:
                            with open(file_path, 'r', encoding='utf-8') as file:
                                file_content = file.read().strip()
                                if file_content:  # Only append if file has content
                                    if system_message:
                                        system_message = f"{system_message}\n\n{file_content}"
                                    else:
                                        system_message = file_content
                                    ConfigManager.console_print(f"Added fresh file content from {file_path}")
                        except Exception as e:
                            ConfigManager.console_print(f"Error reading system message file: {str(e)}")
                    
                    if not system_message:
                        ConfigManager.console_print("Warning: No system message found, using original transcription")
                        return result
                    
                    ConfigManager.console_print(f"Final system message being sent to LLM: {system_message}")
                    processed_result = self.llm_processor.process_text(result, system_message)
                    if processed_result:
                        result = processed_result.strip()
                        # kleine Nachkorrekturen am LLM-Ergebnis
                        import re
                        # a) evtl. Modell-Vorwort ("... bearbeitete Text:") entfernen
                        _parts = result.split('\n', 1)
                        if len(_parts) == 2 and _parts[0].strip().endswith(':') and len(_parts[0].strip()) < 120:
                            result = _parts[1].strip()
                        # b) umschliessende Anfuehrungszeichen entfernen
                        result = result.strip('"„“”').strip()
                        # c) fehlendes Leerzeichen nach Satzende (bekannter Modell-Fehler)
                        result = re.sub(r'([.!?])(?=[A-Za-zÄÖÜäöü])', r'\1 ', result)
                    else:
                        ConfigManager.console_print("LLM processing failed, using original transcription")
                    
                except Exception as e:
                    ConfigManager.console_print(f"Error processing text through LLM: {str(e)}")
                    return result

            # letztes Ergebnis immer in die Zwischenablage sichern,
            # damit nichts verloren geht, falls der Fokus wegspringt. Mit Windows-Zwischenablage-
            # Verlauf (Win+V) bleibt so jedes Diktat abrufbar.
            try:
                if IS_WINDOWS:
                    win32clipboard.OpenClipboard()
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(result)
                    win32clipboard.CloseClipboard()
                else:
                    clipboard_utils.set_text(result)
            except Exception as e:
                ConfigManager.console_print(f"Clipboard-Sicherung fehlgeschlagen: {str(e)}")
                if IS_WINDOWS:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass

            # Type the result
            self.input_simulator.typewrite(result)
            
            if ConfigManager.get_config_value('misc', 'noise_on_completion'):
                AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

            if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
                self.start_result_thread()
            elif getattr(self, 'auto_restart_after_submit', False):
                self.auto_restart_after_submit = False
                self.start_result_thread()
            else:
                self.key_listener.start()

        finally:
            # Re-enable key listener
            if self.key_listener:
                self.key_listener.start()

    def handle_text_cleanup(self):
        """Handle the text selection cleanup shortcut."""
        if not self.llm_processor:
            return

        # macOS/Linux take a text-only clipboard path (no win32clipboard).
        if not IS_WINDOWS:
            self._handle_text_cleanup_posix()
            return

        # Store all clipboard formats
        saved_formats = {}
        win32clipboard.OpenClipboard()
        
        try:
            # Get the list of available formats
            format_id = win32clipboard.EnumClipboardFormats(0)
            while format_id:
                try:
                    data = win32clipboard.GetClipboardData(format_id)
                    saved_formats[format_id] = data
                except:
                    pass  # Skip formats we can't handle
                format_id = win32clipboard.EnumClipboardFormats(format_id)
            
            # Get the text content
            clipboard_text = saved_formats.get(win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()

            if not clipboard_text:
                ConfigManager.console_print("No text in clipboard")
                return
            
            ConfigManager.console_print(f"Processing clipboard text: {clipboard_text[:100]}...")
            
            # Get the base system message
            base_message = ConfigManager.get_config_value("llm_post_processing", "text_cleanup_system_message")
            file_path = ConfigManager.get_config_value("llm_post_processing", "text_cleanup_system_message_file_path")
            
            # Start with a clean system message
            system_message = base_message.strip() if base_message else ""
            
            ConfigManager.console_print(f"Base cleanup system message: {system_message}")
            
            # Append file contents if file path exists and is not empty
            if file_path and os.path.exists(file_path):
                try:
                    ConfigManager.console_print(f"Reading cleanup instructions from file: {file_path}")
                    with open(file_path, 'r', encoding='utf-8') as file:
                        file_content = file.read().strip()
                        if file_content:  # Only append if file has content
                            if system_message:
                                system_message = f"{system_message}\n\n{file_content}"
                            else:
                                system_message = file_content
                            ConfigManager.console_print("Successfully added file content to cleanup instructions")
                except Exception as e:
                    ConfigManager.console_print(f"Error reading cleanup system message file: {str(e)}")
            
            if not system_message:
                ConfigManager.console_print("Warning: No cleanup system message found")
                return
            
            ConfigManager.console_print(f"Final cleanup system message: {system_message}")
            
            # Run through LLM cleanup
            cleaned_text = self.llm_processor.process_text(clipboard_text, system_message)
            
            if cleaned_text and cleaned_text != clipboard_text:
                try:
                    # Simulate keyboard events with proper cleanup
                    keyboard = Controller()
                    
                    # First set the cleaned text to clipboard
                    win32clipboard.OpenClipboard()
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(cleaned_text)
                    win32clipboard.CloseClipboard()
                    
                    try:
                        # Delete selected text and paste cleaned text
                        keyboard.press(Key.delete)
                        keyboard.release(Key.delete)
                        time.sleep(0.1)  # Small delay after delete
                        
                        with keyboard.pressed(Key.ctrl):
                            keyboard.press('v')
                            keyboard.release('v')
                        
                        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
                            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)
                            
                    finally:
                        # Ensure all keys are released
                        keyboard.release(Key.ctrl)
                        keyboard.release('v')
                        keyboard.release(Key.delete)
                        
                        # Restore original clipboard content
                        time.sleep(0.1)  # Wait for paste to complete
                        win32clipboard.OpenClipboard()
                        win32clipboard.EmptyClipboard()
                        for format_id, data in saved_formats.items():
                            try:
                                win32clipboard.SetClipboardData(format_id, data)
                            except:
                                pass  # Skip if we can't restore a particular format
                        win32clipboard.CloseClipboard()
                        
                    # Clear the key chord state
                    self.key_listener.text_cleanup_chord.pressed_keys.clear()
                    
                except Exception as e:
                    ConfigManager.console_print(f"Error simulating keyboard: {str(e)}")
            else:
                ConfigManager.console_print("Text unchanged after LLM processing")
            
        except Exception as e:
            ConfigManager.console_print(f"Error cleaning text: {str(e)}")
        finally:
            try:
                win32clipboard.CloseClipboard()
            except:
                pass  # Ensure clipboard is closed even if an error occurred
            # Ensure key listener is restarted
            self.key_listener.start()

    def _build_cleanup_system_message(self):
        """Assemble the text-cleanup system message from config + optional file."""
        base_message = ConfigManager.get_config_value("llm_post_processing", "text_cleanup_system_message")
        file_path = ConfigManager.get_config_value("llm_post_processing", "text_cleanup_system_message_file_path")
        system_message = base_message.strip() if base_message else ""
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    file_content = file.read().strip()
                    if file_content:
                        system_message = f"{system_message}\n\n{file_content}" if system_message else file_content
            except Exception as e:
                ConfigManager.console_print(f"Error reading cleanup system message file: {str(e)}")
        return system_message

    def _handle_text_cleanup_posix(self):
        """
        macOS/Linux version of handle_text_cleanup: text-only clipboard, uses
        the OS-correct paste keystroke (Cmd+V on macOS). No format preservation.
        """
        try:
            clipboard_text = clipboard_utils.get_text()
            if not clipboard_text:
                ConfigManager.console_print("No text in clipboard")
                return

            ConfigManager.console_print(f"Processing clipboard text: {clipboard_text[:100]}...")
            system_message = self._build_cleanup_system_message()
            if not system_message:
                ConfigManager.console_print("Warning: No cleanup system message found")
                return

            cleaned_text = self.llm_processor.process_text(clipboard_text, system_message)
            if not cleaned_text or cleaned_text == clipboard_text:
                ConfigManager.console_print("Text unchanged after LLM processing")
                return

            try:
                keyboard = Controller()
                saved = clipboard_utils.get_text()
                clipboard_utils.set_text(cleaned_text)
                try:
                    # Delete the selected text, then paste the cleaned text.
                    keyboard.press(Key.delete)
                    keyboard.release(Key.delete)
                    time.sleep(0.1)
                    clipboard_utils.send_paste(keyboard)
                    if ConfigManager.get_config_value('misc', 'noise_on_completion'):
                        AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)
                finally:
                    time.sleep(0.1)  # Wait for paste to complete
                    clipboard_utils.set_text(saved)
                self.key_listener.text_cleanup_chord.pressed_keys.clear()
            except Exception as e:
                ConfigManager.console_print(f"Error simulating keyboard: {str(e)}")
        except Exception as e:
            ConfigManager.console_print(f"Error cleaning text: {str(e)}")
        finally:
            self.key_listener.start()

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    app = QalamApp()
    app.run()
