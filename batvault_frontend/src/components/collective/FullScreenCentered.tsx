export default function FullScreenCentered({ children }: { children: React.ReactNode }) {
    return (
      <div className="min-h-screen w-full bg-black relative overflow-hidden flex flex-col">
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        </div>
  
        <div className="relative z-10 flex-grow flex flex-col">{children}</div>
      </div>
    );
  }
  