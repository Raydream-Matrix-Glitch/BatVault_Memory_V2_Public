// src/pages/VaultPage.tsx
import { useEffect, useState, useRef } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import VaultUI from "./VaultUI";
import CollectiveLayout from "./CollectiveLayout";
import { useIdleLogout } from "./useIdleLogout";

interface FileMeta {
  filename: string;
  path: string;
  tags: string[];
  is_public: boolean;
}

const FILES_URL = "https://auth.scalable-me.com/files";
const UPLOAD_URL = "https://auth.scalable-me.com/upload";

export default function VaultPage() {
  const [files, setFiles] = useState<FileMeta[]>([]);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTags, setUploadTags] = useState<string>("");
  const [selectedPath, setSelectedPath] = useState<string>("");
  const [toast, setToast] = useState<string>("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const token = localStorage.getItem("access_token");

  useIdleLogout(); // default 3 min

  useEffect(() => {
    if (!token) {
      navigate("/");
      return;
    }

    axios
      .get<FileMeta[]>(FILES_URL, {
        headers: { Authorization: `Bearer ${token}` },
      })
      .then((res) => setFiles(res.data))
      .catch(() => {
        localStorage.removeItem("access_token");
        navigate("/");
      });
  }, [navigate, token]);

  const handleUpload = async () => {
    if (!uploadFile || !selectedPath) return;

    const cleanPath = selectedPath.replace(/^\/mnt\/data\//, "").replace(/^\/+|\/+$/g, "");
    const formData = new FormData();
    formData.append("file", uploadFile);
    formData.append("path", cleanPath);
    formData.append("tags", uploadTags);

    try {
      const response = await axios.post<FileMeta>(UPLOAD_URL, formData, {
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "multipart/form-data",
        },
      });

      setFiles((prev) => [...prev, response.data]);
      setToast("✅ Upload successful");
      setUploadFile(null);
      setUploadTags("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch {
      setToast("❌ Upload failed");
    } finally {
      setTimeout(() => setToast(""), 3000);
    }
  };

  const handleDownload = async (filename: string) => {
    if (!token) return;

    try {
      const response = await fetch(`https://auth.scalable-me.com/download/${filename}`, {
        headers: { Authorization: `Bearer ${token}` }
      });

      if (!response.ok) throw new Error("Download failed");

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {
      setToast("❌ Download failed");
      setTimeout(() => setToast(""), 3000);
    }
  };

  const handleDeleteFile = async (filename: string) => {
    if (!token) return;

    try {
      await axios.delete(
        `https://auth.scalable-me.com/file/${filename}`,
        {
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      setFiles((prev) => prev.filter((file) => file.filename !== filename));
      setToast(`🗑️ File "${filename}" deleted`);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        setFiles((prev) => prev.filter((file) => file.filename !== filename));
        setToast(`⚠️ File "${filename}" was already deleted`);
      } else {
        const msg = err?.response?.data?.detail || "Unknown error";
        setToast(`❌ Delete failed: ${msg}`);
      }
    } finally {
      setTimeout(() => setToast(""), 3000);
    }
  };

  const handleDeleteFolder = async (path: string) => {
    if (!token) return;

    const normalized = path.replace(/\/$/, "");

    try {
      await axios.delete(
        `https://auth.scalable-me.com/folder/${normalized}`,
        {
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      setFiles((prev) => prev.filter((file) => file.path !== normalized));
      setToast(`🗑️ Folder "${path}" deleted`);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        setFiles((prev) => prev.filter((file) => file.path !== normalized));
        setToast(`⚠️ Folder "${path}" was already deleted`);
      } else {
        const msg = err?.response?.data?.detail || "Unknown error";
        setToast(`❌ Folder delete failed: ${msg}`);
      }
    } finally {
      setTimeout(() => setToast(""), 3000);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file) setUploadFile(file);
  };

  const logout = () => {
    localStorage.removeItem("access_token");
    navigate("/");
  };

  return (
    <CollectiveLayout>
      <div className="flex justify-center px-2">
        <div
          className="p-4 sm:p-6 space-y-6 text-white w-full max-w-6xl"
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
        >
          <div className="flex justify-between items-center border-b border-gray-700 pb-4">
            <h2 className="text-2xl font-bold tracking-tight">🔐 Your Vault</h2>
            <button onClick={logout} className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg">
              Logout
            </button>
          </div>

          <div className="bg-[#121212] border-t border-gray-800 rounded-xl px-6 py-4 shadow-sm space-y-3">
            <div className="text-sm text-gray-400">
              📁 <span className="text-white font-mono">{selectedPath || "Root"}</span>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
              className="block w-full text-sm text-gray-300 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-vaultred file:text-white hover:file:bg-red-600"
            />

            <input
              type="text"
              placeholder="Enter tags (comma-separated)"
              value={uploadTags}
              onChange={(e) => setUploadTags(e.target.value)}
              className="block w-full mt-2 text-sm p-2 rounded bg-[#1e1e1e] border border-gray-600 text-white"
            />

            <button
              onClick={handleUpload}
              className="w-full bg-vaultred/90 hover:bg-vaultred text-white py-2 px-4 rounded-md font-semibold shadow"
            >
              ⬆️ Upload
            </button>

            <p className="text-xs text-gray-500 text-center">
              You can also drag and drop a file anywhere on the screen.
            </p>
          </div>

          {toast && (
            <div className="fixed top-4 right-4 z-50 px-5 py-3 bg-[#2a2a2a] border-l-4 border-vaultred text-white rounded shadow-lg text-sm">
              {toast}
            </div>
          )}

          <div className="pt-2">
            <VaultUI
              files={files}
              onPathSelect={setSelectedPath}
              onDownload={handleDownload}
              onDeleteFile={handleDeleteFile}
              onDeleteFolder={handleDeleteFolder}
            />
          </div>
        </div>
      </div>
    </CollectiveLayout>
  );
}
