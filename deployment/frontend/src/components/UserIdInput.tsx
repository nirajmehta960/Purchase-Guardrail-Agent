import { useEffect, useState } from "react";
import { User } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useUser } from "../context/UserContext";

export function UserIdInput() {
  const { userId, setUserId, profileError, isLoadingProfile } = useUser();
  const [draft, setDraft] = useState(userId);

  useEffect(() => {
    setDraft(userId);
  }, [userId]);

  const apply = () => {
    setUserId(draft.trim());
  };

  return (
    <div className="flex flex-col items-end gap-1 max-w-[min(100%,20rem)]">
      <div className="flex items-center gap-2 w-full">
        <User className="w-4 h-4 text-muted-foreground shrink-0 hidden sm:block" />
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && apply()}
          placeholder="User ID (e.g. user_001)"
          className="h-9 text-sm bg-secondary/40 border-border/60"
          aria-label="User ID"
        />
        <Button type="button" size="sm" variant="secondary" className="shrink-0" onClick={apply}>
          Set
        </Button>
      </div>
      {isLoadingProfile && userId && (
        <p className="text-[10px] text-muted-foreground">Loading profile…</p>
      )}
      {profileError && userId && (
        <p className="text-[10px] text-destructive text-right max-w-xs">{profileError}</p>
      )}
      {userId && !profileError && !isLoadingProfile && (
        <p className="text-[10px] text-muted-foreground truncate max-w-full" title={userId}>
          Active: <span className="text-primary font-medium">{userId}</span>
        </p>
      )}
    </div>
  );
}
