import csv
import os

class LocalizationManager:
    def __init__(self, csv_path="stringtable.csv", default_lang="Language_en"):
        self.csv_path = csv_path
        self.default_lang = default_lang
        self.active_lang = default_lang
        self.translations = {}
        
        # Human-readable display names mapped directly to CSV column headers
        self.language_mapping = {
            "English": "Language_en",
            "Čeština": "Language_cz",
            "Deutsch": "Language_de",
            "Русский": "Language_ru",
            "Español": "Language_es",
            "Français": "Language_fr"
        }
        
        self.load_stringtable()

    def load_stringtable(self):
        """Loads the stringtable.csv file into memory."""
        if not os.path.exists(self.csv_path):
            print(f"[Warning] Localization file not found at: {self.csv_path}. Using fallback strings.")
            return

        try:
            with open(self.csv_path, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader)  # Header row: Language, Language_en, Language_cz...
                
                # Create a map of column index -> language key
                col_to_lang = {i: lang.strip() for i, lang in enumerate(header)}
                
                for row in reader:
                    if not row or row[0].startswith("#"):
                        continue  # Skip empty lines and comments
                    
                    key = row[0].strip()
                    self.translations[key] = {}
                    
                    for i in range(1, len(row)):
                        if i in col_to_lang:
                            lang_key = col_to_lang[i]
                            self.translations[key][lang_key] = row[i].strip()
        except Exception as e:
            print(f"[Error] Failed to read stringtable.csv: {e}")

    def set_language_by_name(self, display_name):
        """Sets the active language using the human-readable display name."""
        if display_name in self.language_mapping:
            self.active_lang = self.language_mapping[display_name]

    def translate(self, key, fallback_text=""):
        """Looks up a key in the active language dictionary, falling back gracefully if missing."""
        if key in self.translations:
            # 1. Try target active language
            text = self.translations[key].get(self.active_lang)
            if text:
                return text
            # 2. Fall back to default language (English)
            text = self.translations[key].get(self.default_lang)
            if text:
                return text
                
        # 3. Fall back to hardcoded layout string if key/translation completely missing
        return fallback_text