import { useMemo, useState } from "react";

interface FileMeta {
  filename: string;
  path: string;
  tags: string[];
  is_public: boolean;
}

interface VaultExplorerProps {
  files: FileMeta[];
  onPathSelect: (path: string) => void;
  onDownload: (filename: string) => void;
  onDeleteFile: (filename: string) => void;
  onDeleteFolder: (path: string) => void;
}

export default function VaultExplorer({
  files,
  onPathSelect,
  onDownload,
  onDeleteFile,
  onDeleteFolder,
}: VaultExplorerProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [newFolder, setNewFolder] = useState<string>("");

  const allTags = useMemo(() => {
    const tagSet = new Set<string>();
    files.forEach((file) => file.tags.forEach((t) => tagSet.add(t)));
    return Array.from(tagSet);
  }, [files]);

  const [activeTags, setActiveTags] = useState<string[]>([]);

  const toggleTag = (tag: string) => {
    setActiveTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const filteredFiles = useMemo(() => {
    if (activeTags.length === 0) return files;
    return files.filter((file) =>
      activeTags.some((tag) => file.tags.includes(tag))
    );
  }, [files, activeTags]);

  const grouped = useMemo(() => {
    const map: Record<string, FileMeta[]> = {};
    for (const file of filteredFiles) {
      const cleanPath = (file.path || "").replace(/\\/g, "/").replace(/\/$/, "");
      if (!map[cleanPath]) map[cleanPath] = [];
      map[cleanPath].push(file);
    }
    return map;
  }, [filteredFiles]);

  return (
    <div className="flex flex-col max-h-screen overflow-auto rounded-xl overflow-hidden border border-gray-700 shadow-2xl bg-[#0c0c0c] text-white">
      {/* Tag Filter Bar */}
      {allTags.length > 0 && (
        <div className="px-6 pt-4 pb-2 border-b border-gray-700 bg-[#111]">
          <div className="text-sm text-gray-400 mb-1">Filter by tags:</div>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => (
              <button
                key={tag}
                onClick={() => toggleTag(tag)}
                className={`px-2 py-1 rounded-full text-xs font-medium ${
                  activeTags.includes(tag)
                    ? "bg-vaultred text-white"
                    : "bg-[#2a2a2a] text-gray-300 hover:bg-vaultred/30"
                }`}
              >
                #{tag}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col md:flex-row flex-1 overflow-hidden">
        {/* Folder Sidebar */}
        <div className="w-full md:w-1/4 p-4 border-r border-gray-700 bg-[#121212] space-y-4">
          <div>
            <h3 className="font-bold text-base sm:text-lg md:text-xl mb-2">📁 Folders</h3>
            <div className="flex items-center gap-1">
              <input
                type="text"
                value={newFolder}
                onChange={(e) => setNewFolder(e.target.value)}
                placeholder="New folder name"
                className="flex-1 text-sm p-2 rounded bg-[#1e1e1e] border border-gray-600 text-white"
              />
              <button
                onClick={() => {
                  const trimmed = newFolder.trim().replace(/\\/g, "/").replace(/\/$/, "");
                  if (!trimmed || grouped[trimmed]) return;
                  setSelectedPath(trimmed);
                  onPathSelect(trimmed);
                  setNewFolder("");
                }}
                className="px-2 py-1 bg-vaultred hover:bg-red-600 text-white text-sm rounded"
                aria-label="Create new folder"
              >
                <span className="text-lg font-bold leading-none">+</span>
              </button>
            </div>
          </div>

          <div className="space-y-1 max-h-[calc(100%-100px)] overflow-y-auto pr-1">
            {Object.keys(grouped).map((path) => (
              <div key={path} className="flex items-center justify-between group">
                <button
                  onClick={() => {
                    const newPath = selectedPath === path ? null : path;
                    setSelectedPath(newPath);
                    onPathSelect(newPath || "");
                  }}
                  className={`flex-1 text-left px-3 py-2 rounded-md text-sm transition ${
                    selectedPath === path
                      ? "border-l-4 border-vaultred bg-[#1e1e1e] font-semibold"
                      : "hover:bg-vaultred/10"
                  }`}
                >
                  {path || "/"}
                </button>
                <button
                  onClick={() => onDeleteFolder(path)}
                  className="ml-2 text-red-400 hover:text-red-600 text-sm invisible group-hover:visible"
                  title="Delete folder"
                >
                  🗑️
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* File Display Panel */}
        <div className="w-full md:flex-1 px-6 py-4 overflow-y-auto">
          {selectedPath && (
            <div className="text-gray-400 text-sm mb-3 flex items-center gap-2">
              📂 <span className="text-white font-mono">{selectedPath}</span>
            </div>
          )}

          {(selectedPath && (grouped[selectedPath] || []).length > 0) ? (
            <div className="rounded-xl bg-[#101010] px-6 py-4 border border-gray-800">
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
                {grouped[selectedPath].map((file) => (
                  <div
                    key={file.filename}
                    className="bg-[#1a1a1a] rounded-lg p-4 border border-gray-700 hover:border-vaultred hover:shadow-md transition-transform hover:scale-105 flex flex-col justify-between space-y-2"
                  >
                    <p className="text-xs sm:text-sm md:text-base text-gray-200 font-mono truncate">
                      📄 {file.filename}
                    </p>
                    <div className="flex justify-between items-center mt-auto">
                      <button
                        onClick={() => onDownload(file.filename)}
                        className="text-vaultred text-sm underline hover:text-red-400"
                      >
                        Download
                      </button>
                      <div className="flex items-center gap-2">
                        <span className="text-gray-500 text-[10px] sm:text-xs">({file.tags.length} tags)</span>
                        <button
                          onClick={() => onDeleteFile(file.filename)}
                          className="text-red-400 hover:text-red-600 text-sm"
                          title="Delete file"
                        >
                          🗑️
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : selectedPath ? (
            <div className="text-gray-500 italic flex items-center gap-2 mt-8">
              🗃️ This folder is empty.
            </div>
          ) : (
            <p className="text-gray-500 italic mt-8">Select a folder to view files.</p>
          )}
        </div>
      </div>
    </div>
  );
}
