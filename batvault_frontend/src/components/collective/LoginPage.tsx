// src/pages/LoginPage.tsx
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import LoginForm from "./LoginForm";
import CollectiveLayout from "./CollectiveLayout";

function isTokenValid(token: string | null): boolean {
  if (!token) return false;
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp && payload.exp > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

export default function LoginPage() {
  const navigate = useNavigate();

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (isTokenValid(token)) {
      navigate("/vault");
    } else {
      localStorage.removeItem("access_token");
    }
  }, [navigate]);

  return (
    <CollectiveLayout>
      <div className="flex-grow flex items-center justify-center p-4 relative">
        {/* subtle animated background */}
        <div className="absolute inset-0 z-0 bg-gradient-to-br from-vaultred/10 via-transparent to-vaultred/10 rounded-2xl blur-3xl opacity-10" />

        {/*  ← pulsing neon border box */}
        <motion.div
          className="w-full max-w-md bg-black/50 backdrop-blur-sm rounded-2xl shadow-xl p-8 space-y-6 z-10 relative"
          style={{
            borderStyle: "solid",
            borderWidth: "1px",
            borderColor: "#18ffffff",
          }}
          initial={{
          boxShadow: "0 0 5px rgba(24,247,255,0.6), 0 0 5px rgba(24,247,255,0.6)",
            }}
          animate={{
            // animate box-shadow to simulate border glow pulsing
          boxShadow: "0 0 16px rgba(24,247,255,0.8), 0 0 32px rgba(24,247,255,0.8)",
          }}
          transition={{
          duration: 1,
          ease: "easeInOut",
          repeat: Infinity,
          repeatType: "reverse",
          }}
        >
          {/* status indicator */}
          <motion.div
            className="absolute top-4 right-4 flex items-center space-x-2"
            aria-label="status"
          >
            {/* the dot */}
            <motion.div
              className="w-3 h-3 rounded-full bg-green-500"
              animate={{ opacity: [0.3, 1, 0.3] }}
              transition={{
                duration: 2,        // ← control your pulse speed here
                ease: "easeInOut",
                repeat: Infinity,
              }}
            />
            {/* the label in the same font & style as your heading */}
            <span
              className="text-xs font-mono"
              style={{ color: "#ff1808ff" }}
            >
              ENCRYPTED
            </span>
          </motion.div>

          <h2
            className="text-3xl font-bold drop-shadow-md text-center font-mono"
            style={{ color: "#ff0000", fontFamily: "Courier New, monospace" }}
          >
            BatVault<br />
            Collective
          </h2>
          <p className="text-sm text-gray-400 text-center italic">
            Secure. Shared. Sacred.
          </p>

          <LoginForm />
        </motion.div>
      </div>
    </CollectiveLayout>
  );
}
