# Vendored from incompetagents-google/measurement/taxonomy.py (feat/measurement-integration).
import json
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class Taxonomy:
    def __init__(self, json_path: Path):
        self.json_path = json_path
        self.data = self.load()

    def load(self) -> dict[str, Any]:
        if not self.json_path.exists():
            return {"version": "0.0.0", "categories": []}
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.json_path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def get_category(self, category_id: str) -> Optional[dict[str, Any]]:
        for cat in self.data.get("categories", []):
            if cat.get("id") == category_id:
                return cat
        return None

    def add_category(self, category: dict[str, Any]) -> None:
        """Allows an LLM agent or script to statefully expand the taxonomy."""
        if not category.get("id"):
            raise ValueError("Category must have an 'id'.")
        if self.get_category(category["id"]):
            raise ValueError(f"Category {category['id']} already exists.")
            
        categories = self.data.setdefault("categories", [])
        categories.append(category)
        
        # Increment minor version
        v_parts = self.data.get("version", "1.0.0").split(".")
        if len(v_parts) >= 2:
            v_parts[1] = str(int(v_parts[1]) + 1)
        else:
            v_parts.append("1")
        self.data["version"] = ".".join(v_parts)
        self.data["last_updated"] = utc_now()
        self.save()

    def render_markdown_string(self) -> str:
        """Generates human-readable taxonomy markdown string."""
        lines = ["# Taxonomy", ""]
        for cat in sorted(self.data.get("categories", []), key=lambda c: c.get("id", "")):
            cat_id = cat.get("id", "UNKNOWN")
            name = cat.get("name", "Unknown")
            lines.append(f"## {cat_id}: {name}")
            lines.append("")
            
            keys_order = [
                "id", "name", "definition", "inclusion_criteria", 
                "exclusion_criteria", "typical_evidence", 
                "near-miss_examples", "severity_guidance", "related_categories"
            ]
            
            printed_keys = set()
            for key in keys_order:
                val = cat.get(key)
                if val is not None:
                    printed_keys.add(key)
                    if key == "id":
                        lines.append(f"- ID: `{val}`")
                    elif key == "related_categories":
                        if isinstance(val, list):
                            val_str = ", ".join(f"`{x}`" for x in val)
                        else:
                            val_str = str(val)
                        lines.append(f"- Related categories: {val_str}")
                    else:
                        nice_key = key.replace("_", " ").capitalize()
                        lines.append(f"- {nice_key}: {val}")
            
            for key, val in cat.items():
                if key not in printed_keys and val is not None:
                    nice_key = key.replace("_", " ").capitalize()
                    lines.append(f"- {nice_key}: {val}")
            lines.append("")
        return "\n".join(lines)

    def export_markdown(self, md_path: Path) -> None:
        """Generates human-readable taxonomy.md file from JSON."""
        md_path.write_text(self.render_markdown_string(), encoding="utf-8")
