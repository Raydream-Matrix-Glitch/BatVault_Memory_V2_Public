// src/utils/auth.ts
export function isTokenValid(token: string | null): boolean {
    if (!token) return false;
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      return typeof payload.exp === "number" && payload.exp > Math.floor(Date.now() / 1000);
    } catch {
      return false;
    }
  }
  
  // Optional: auto-logout watcher (set up in App.tsx)
  export function monitorTokenExpiry(interval = 60000): void {
    const token = localStorage.getItem("access_token");
    if (!token) return; // ⛔ no token, no need to monitor
  
    const check = () => {
      const token = localStorage.getItem("access_token");
      if (!isTokenValid(token)) {
        localStorage.removeItem("access_token");
        window.location.href = "/";
      }
    };
    setInterval(check, interval);
  }
  
  