import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

export function useIdleLogout(timeoutMs = 3 * 60 * 1000) {
  const navigate = useNavigate();

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;

    const resetTimer = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        localStorage.removeItem("access_token");
        navigate("collective/expired", { replace: true });
      }, timeoutMs);
    };

    const events = ["mousemove", "keydown", "click", "scroll"];

    events.forEach((e) => document.addEventListener(e, resetTimer));

    resetTimer(); // Initialize the first timer

    return () => {
      clearTimeout(timer);
      events.forEach((e) => document.removeEventListener(e, resetTimer));
    };
  }, [navigate, timeoutMs]);
}
