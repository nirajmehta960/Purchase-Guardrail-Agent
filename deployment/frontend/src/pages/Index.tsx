import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { MessageSquare, LayoutDashboard, BarChart3 } from "lucide-react";
import { AiChat } from "@/components/AiChat";
import { FinancialDashboard } from "@/components/FinancialDashboard";
import { ProductAnalysis } from "@/components/ProductAnalysis";
import { UserIdInput } from "@/components/UserIdInput";

const tabs = [
  { id: "chat", label: "AI Advisor", icon: MessageSquare },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "analysis", label: "Analysis", icon: BarChart3 },
] as const;

type TabId = (typeof tabs)[number]["id"];

const Index = () => {
  const [activeTab, setActiveTab] = useState<TabId>("chat");

  return (
    <div className="min-h-screen bg-background gradient-glow">
      {/* Header */}
      <header className="border-b border-border/50 backdrop-blur-md bg-background/80 sticky top-0 z-50">
        <div className="container flex items-center justify-between h-16 px-4 md:px-6">
          <div className="flex items-center">
            <img src="/savvio-wordmark.svg" alt="SavVio" className="h-8 w-auto" />
          </div>

          <div className="flex flex-wrap items-center gap-3 flex-1 justify-end min-w-0">
            <UserIdInput />
            <nav className="flex items-center gap-1 bg-secondary/50 rounded-lg p-1 shrink-0">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`relative flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md transition-colors ${
                  activeTab === tab.id
                    ? "text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {activeTab === tab.id && (
                  <motion.div
                    layoutId="activeTab"
                    className="absolute inset-0 bg-primary rounded-md"
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
        </div>
      </header>

      {/* Content */}
      <main className="container px-4 md:px-6 py-6">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.25 }}
          >
            {activeTab === "chat" && <AiChat />}
            {activeTab === "dashboard" && <FinancialDashboard />}
            {activeTab === "analysis" && <ProductAnalysis />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
};

export default Index;
