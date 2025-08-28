// src/routes/MemoryRoute.tsx
import MemoryLayout from "../components/memory/MemoryLayout";
import MemoryPage from "../components/memory/MemoryPage";

export default function MemoryRoute() {
  return (
    <MemoryLayout>
      {/* Mount the interactive Memory page */}
      <MemoryPage />
    </MemoryLayout>
  );
}
