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
    <div className="h-screen flex flex-col bg-background gradient-glow overflow-hidden">
      {/* Header */}
      <header className="shrink-0 border-b border-border/50 backdrop-blur-md bg-background/80 z-50">
        <div className="container flex items-center min-h-20 py-3 px-4 md:px-6 gap-4">
          <button 
            onClick={() => setActiveTab("chat")}
            className="flex items-center shrink-0 hover:opacity-90 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-lg"
            aria-label="Return to AI Advisor"
          >
            <img src="/savvio-logo.png" alt="SavVio" className="h-[4.5rem] md:h-[5.5rem] w-auto drop-shadow-md" />
          </button>

          <div className="flex items-center gap-4 flex-1 justify-center min-w-0">
            <UserIdInput />
          </div>

          <nav className="flex items-center gap-2 bg-secondary/50 rounded-xl p-1.5 shrink-0">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`relative flex items-center gap-2 px-5 py-2.5 text-base font-semibold rounded-lg transition-colors ${
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
                <span className="relative z-10 flex items-center gap-2">
                  <tab.icon className="w-4 h-4" />
                  <span className="hidden sm:inline">{tab.label}</span>
                </span>
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* Content */}
      <main className="container flex-1 min-h-0 px-4 md:px-6 py-6 pb-0">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            className="h-full"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.25 }}
          >
            {activeTab === "chat" && <AiChat />}
            {activeTab === "dashboard" && <FinancialDashboard />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
};

export default Index;
