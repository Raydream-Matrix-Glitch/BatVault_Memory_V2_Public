import React from "react";

export interface TagCloudProps {
  /** Map of tag to occurrence count */
  tags: Record<string, number>;
  /** Currently selected tag for filtering */
  selected?: string;
  /** Callback invoked when a tag is selected; pass undefined to clear */
  onSelect: (tag?: string) => void;
}

/**
 * TagCloud renders a collection of tags sized relative to their frequency. The
 * user can click a tag to apply a filter; clicking again on the selected tag
 * clears the filter. Use only CSS and simple math (no heavy libs).
 */
const TagCloud: React.FC<TagCloudProps> = ({ tags, selected, onSelect }) => {
  // Compute min and max counts to scale font sizes linearly
  const entries = Object.entries(tags);
  if (entries.length === 0) return null;
  const counts = entries.map(([, count]) => count);
  const min = Math.min(...counts);
  const max = Math.max(...counts);
  const range = max - min || 1;
  // Determine font size for a given count between 0.8rem and 1.4rem
  const sizeFor = (count: number) => {
    const scale = (count - min) / range;
    const minSize = 0.8;
    const maxSize = 1.4;
    return (minSize + (maxSize - minSize) * scale).toFixed(2) + "rem";
  };
  return (
    <div className="flex flex-wrap gap-2 bg-surface border border-vaultred/30 rounded-md p-3 max-h-40 overflow-y-auto">
      {entries.map(([tag, count]) => {
        const active = selected === tag;
        return (
          <button
            key={tag}
            type="button"
            onClick={() => onSelect(active ? undefined : tag)}
            className={`transition-colors focus:outline-none ${
              active
                ? "text-vaultred underline"
                : "text-neonCyan hover:text-vaultred"
            }`}
            style={{ fontSize: sizeFor(count) }}
          >
            {tag}
          </button>
        );
      })}
    </div>
  );
};

export default TagCloud;