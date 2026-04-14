import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { MessageSquare, LayoutDashboard } from "lucide-react";
import { AiChat } from "@/components/AiChat";
import { FinancialDashboard } from "@/components/FinancialDashboard";
import { UserIdInput } from "@/components/UserIdInput";

const tabs = [
  { id: "chat", label: "AI Advisor", icon: MessageSquare },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
] as const;

type TabId = (typeof tabs)[number]["id"];

const Index = () => {
  const [activeTab, setActiveTab] = useState<TabId>("chat");

  return (
    <div className="h-[100dvh] flex flex-col bg-background gradient-glow overflow-hidden">
      {/* Header */}
      <header className="shrink-0 border-b border-border/50 backdrop-blur-md bg-background/80 z-50 sticky top-0">
        <div className="container flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4 py-3">
          <button
            onClick={() => setActiveTab("chat")}
            className="flex items-center shrink-0 hover:opacity-90 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-lg self-start sm:self-auto"
            aria-label="Return to AI Advisor"
          >
            <img
              src="/savvio-logo.png"
              alt="SavVio"
              className="h-12 sm:h-14 md:h-[4.5rem] w-auto drop-shadow-md"
            />
          </button>

          <div className="flex items-center gap-4 flex-1 justify-center min-w-0 w-full sm:w-auto">
            <UserIdInput />
          </div>

          <nav className="shrink-0 w-full sm:w-auto overflow-x-auto [-webkit-overflow-scrolling:touch]">
            <div className="inline-flex items-center gap-2 bg-secondary/50 rounded-xl p-1.5 min-w-max">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`relative flex items-center gap-2 px-3 sm:px-5 py-2 text-sm sm:text-base font-semibold rounded-lg transition-colors ${
                    activeTab === tab.id
                      ? "text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
                  }`}
                >
                  {activeTab === tab.id && (
                    <motion.div
                      layoutId="activeTab"
                      className="absolute inset-0 bg-primary rounded-lg"
                      transition={{ type: "spring", duration: 0.4, bounce: 0.15 }}
                    />
                  )}
                  <span className="relative z-10 flex items-center justify-center gap-2 min-w-10">
                    <tab.icon className="w-4 h-4 shrink-0" />
                    <span className="hidden min-[380px]:inline text-[11px] leading-none sm:text-sm">
                      {tab.id === "chat" ? "Advisor" : "Dashboard"}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </nav>
        </div>
      </header>

      {/* Content — one full-width scroll region (scrollbar on viewport edge) for both tabs */}
      <main className="flex-1 min-h-0 min-w-0 flex flex-col">
        <div
          id="main-scroll"
          className="flex-1 min-h-0 w-full overflow-y-auto overflow-x-hidden scrollbar-thin [-webkit-overflow-scrolling:touch]"
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              className="container mx-auto w-full max-w-full px-4 sm:px-6 py-4 sm:py-6 pb-8"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.25 }}
            >
              {activeTab === "chat" && <AiChat />}
              {activeTab === "dashboard" && <FinancialDashboard />}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
};

export default Index;
