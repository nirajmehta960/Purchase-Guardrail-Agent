import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { fetchUserProfile, type UserProfileResponse } from "../services/api";

/**
 * Demo profile loaded on every fresh visit (no localStorage — each page load starts here).
 * Override at build time: `VITE_DEFAULT_USER_ID=U00009 npm run build`
 */
export const DEFAULT_USER_ID =
  (import.meta.env.VITE_DEFAULT_USER_ID as string | undefined)?.trim() || "U00001";

interface UserContextValue {
  userId: string;
  setUserId: (id: string) => void;
  userProfile: UserProfileResponse | null;
  profileError: string | null;
  isLoadingProfile: boolean;
  refetchProfile: () => Promise<void>;
}

const UserContext = createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: ReactNode }) {
  const [userId, setUserIdState] = useState(DEFAULT_USER_ID);
  const [userProfile, setUserProfile] = useState<UserProfileResponse | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [isLoadingProfile, setIsLoadingProfile] = useState(false);

  const setUserId = useCallback((id: string) => {
    const trimmed = id.trim();
    setUserIdState(trimmed || DEFAULT_USER_ID);
  }, []);

  const refetchProfile = useCallback(async () => {
    if (!userId.trim()) {
      setUserProfile(null);
      setProfileError(null);
      return;
    }
    setIsLoadingProfile(true);
    setProfileError(null);
    try {
      const p = await fetchUserProfile(userId.trim());
      setUserProfile(p);
    } catch (e) {
      setUserProfile(null);
      setProfileError(
        e instanceof Error ? e.message : typeof e === "string" ? e : "Failed to load profile",
      );
    } finally {
      setIsLoadingProfile(false);
    }
  }, [userId]);

  useEffect(() => {
    void refetchProfile();
  }, [refetchProfile]);

  const value = useMemo(
    () => ({
      userId,
      setUserId,
      userProfile,
      profileError,
      isLoadingProfile,
      refetchProfile,
    }),
    [userId, setUserId, userProfile, profileError, isLoadingProfile, refetchProfile],
  );

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useUser(): UserContextValue {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
}
