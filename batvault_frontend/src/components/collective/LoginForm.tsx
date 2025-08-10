// src/components/LoginForm.tsx
import { useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";

const AUTH_URL = "https://auth.scalable-me.com/login";

type LoginResponse = {
  access_token?: string;
};

export default function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    try {
      const response = await axios.post<LoginResponse>(AUTH_URL, {
        username,
        password,
      });

      const token = response.data.access_token;
      if (!token) throw new Error("Token not received");

      localStorage.setItem("access_token", token);
      navigate("/collective/success");
    } catch {
      setError("Login failed. Please check your credentials.");
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4 animate-fade-in-up duration-300">
      <input
        type="text"
        placeholder="Username"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        required
        className="w-full px-4 py-2 rounded bg-[#111] text-white placeholder-gray-400 border border-gray-700 focus:outline-none focus:ring-2 focus:ring-vaultred transition-all"
      />
      <input
        type="password"
        placeholder="Password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
        className="w-full px-4 py-2 rounded bg-[#111] text-white placeholder-gray-400 border border-gray-700 focus:outline-none focus:ring-2 focus:ring-vaultred transition-all"
      />
      <button
        type="submit"
        className="w-full py-2 bg-gradient-to-r from-red-700 to-vaultred text-white font-bold rounded hover:scale-105 hover:shadow-lg hover:shadow-vaultred/40 transition"
      >
        Initiate Access
      </button>
      {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
    </form>
  );
}
