import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Activity, Bell, Boxes, BrainCircuit, ChevronDown, CircleHelp, Clock3, Command, FileCode2, FolderGit2, LayoutDashboard, Moon, Search, Settings2, ShieldCheck, Sun } from "lucide-react";
import codewiseMark from "../assets/codewise-mark.svg";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail, SidebarTrigger } from "@/components/ui/sidebar";
import { useSidebar } from "@/components/ui/sidebar-context.js";
import { useTheme } from "@/state/useTheme.js";
import { useSession } from "@/state/session-context.js";

const primaryNavigation = [
  { title: "Overview", url: "/", icon: LayoutDashboard },
  { title: "Repositories", url: "/repositories", icon: FolderGit2 },
  { title: "Analysis history", url: "/history", icon: Clock3 },
];
const intelligenceNavigation = [
  { title: "Agent workspace", url: "/agents", icon: Boxes },
  { title: "Architecture intelligence", url: "/architecture", icon: BrainCircuit, planned: true },
];

function WorkspaceNavLink({ to, end, children, onClick, ...props }) {
  const { setOpenMobile } = useSidebar();

  return (
    <NavLink
      to={to}
      end={end}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        setOpenMobile(false);
        window.requestAnimationFrame(() => {
          document.getElementById("main-content")?.focus();
        });
      }}
      {...props}
    >
      {children}
    </NavLink>
  );
}

function NavigationGroup({ label, items, pathname }) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map(({ title, url, icon: Icon, planned }) => (
            <SidebarMenuItem key={url}>
              <SidebarMenuButton asChild isActive={pathname === url} tooltip={title}>
                <WorkspaceNavLink to={url} end={url === "/"}><Icon aria-hidden="true" /><span>{title}</span>{planned && <span className="sr-only">Planned</span>}</WorkspaceNavLink>
              </SidebarMenuButton>
              {planned && <SidebarMenuBadge><span className="size-1.5 rounded-full bg-muted-foreground/60" aria-hidden="true" /></SidebarMenuBadge>}
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

export default function AppLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { session, signOut, error: sessionError } = useSession();
  const { theme, toggleTheme } = useTheme();
  const [dialog, setDialog] = useState(null);
  const dialogTriggerRef = useRef(null);
  const currentTitle = [...primaryNavigation, ...intelligenceNavigation, { title: "Settings", url: "/settings" }].find((item) => item.url === pathname)?.title ??
    (pathname.startsWith("/executions/") ? (pathname.includes("/agents/") ? "Agent results" : "Execution results") : "CodeWise");
  const ThemeIcon = theme === "dark" ? Sun : Moon;
  const doSignOut = async () => {
    try { await signOut(); navigate("/login", { replace: true }); }
    catch { /* The session context retains the safe API error for display. */ }
  };

  const openDialog = (name, event) => {
    const trigger = event?.currentTarget ?? document.activeElement;
    dialogTriggerRef.current = trigger instanceof HTMLElement && trigger !== document.body
      ? trigger
      : null;
    setDialog(name);
  };

  const restoreDialogFocus = (event) => {
    const trigger = dialogTriggerRef.current;
    if (trigger?.isConnected) {
      event.preventDefault();
      trigger.focus();
    }
  };

  useEffect(() => {
    const onKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        const activeElement = document.activeElement;
        if (
          activeElement instanceof HTMLElement &&
          (activeElement.isContentEditable ||
            ["INPUT", "TEXTAREA", "SELECT"].includes(activeElement.tagName))
        ) {
          return;
        }
        event.preventDefault();
        const trigger = document.activeElement;
        dialogTriggerRef.current = trigger instanceof HTMLElement && trigger !== document.body
          ? trigger
          : null;
        setDialog("search");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <SidebarProvider defaultOpen>
      <Sidebar collapsible="icon" variant="inset" className="border-sidebar-border">
        <SidebarHeader className="px-3 pt-4">
          <WorkspaceNavLink to="/" className="flex min-h-10 items-center gap-3 overflow-hidden px-1" aria-label="CodeWise V2 overview">
            <img src={codewiseMark} alt="" className="size-8 shrink-0 rounded-lg" />
            <span className="truncate text-sm font-semibold tracking-tight">CodeWise <span className="text-primary">V2</span></span>
          </WorkspaceNavLink>
          <div className="flex items-center gap-2 rounded-lg border border-sidebar-border bg-sidebar-accent/60 px-2.5 py-2 group-data-[collapsible=icon]:hidden">
            <span className="flex size-7 items-center justify-center rounded-md bg-background text-muted-foreground"><Command className="size-3.5" /></span>
            <div className="min-w-0 flex-1"><p className="truncate text-xs font-medium">Local workspace</p><p className="text-[11px] text-muted-foreground">Personal</p></div>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </div>
        </SidebarHeader>
        <SidebarContent className="pt-3">
          <NavigationGroup label="Workspace" items={primaryNavigation} pathname={pathname} />
          <NavigationGroup label="Intelligence" items={intelligenceNavigation} pathname={pathname} />
          <SidebarGroup>
            <SidebarGroupLabel>Preferences</SidebarGroupLabel>
            <SidebarGroupContent><SidebarMenu><SidebarMenuItem><SidebarMenuButton asChild isActive={pathname === "/settings"} tooltip="Settings"><WorkspaceNavLink to="/settings"><Settings2 /><span>Settings</span></WorkspaceNavLink></SidebarMenuButton></SidebarMenuItem></SidebarMenu></SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter className="px-3 pb-4">
          <div className="rounded-lg border border-sidebar-border bg-sidebar-accent/40 p-3 group-data-[collapsible=icon]:hidden">
            <div className="flex items-center gap-2 text-xs font-medium"><ShieldCheck className="size-4 text-primary" />Private by design</div>
            <p className="mt-1.5 text-[11px] leading-4 text-muted-foreground">Repository execution remains isolated and permission controlled.</p>
          </div>
          <Separator className="bg-sidebar-border" />
          <SidebarMenu><SidebarMenuItem><SidebarMenuButton tooltip="Help and documentation" onClick={(event) => openDialog("help", event)}><CircleHelp /><span>Help & documentation</span></SidebarMenuButton></SidebarMenuItem></SidebarMenu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="min-h-svh bg-background">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-border/70 bg-background/90 px-4 backdrop-blur-xl sm:px-6">
          <SidebarTrigger className="-ml-1" aria-label="Toggle navigation sidebar" />
          <Separator orientation="vertical" className="h-4" />
          <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{currentTitle}</p><p className="hidden text-[11px] text-muted-foreground sm:block">Code intelligence workspace</p></div>
          <Button variant="outline" size="sm" className="hidden h-8 w-52 justify-between text-muted-foreground md:flex" onClick={(event) => openDialog("search", event)}><span className="flex items-center gap-2"><Search className="size-3.5" />Search workspace</span><kbd className="rounded border border-border px-1 text-[10px]">⌘ K</kbd></Button>
          <Button variant="ghost" size="icon" className="md:hidden" onClick={(event) => openDialog("search", event)} aria-label="Search workspace"><Search /></Button>
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}><ThemeIcon /></Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label="Notifications"><Bell /><span className="sr-only">Notifications</span></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64"><DropdownMenuLabel>Notifications</DropdownMenuLabel><DropdownMenuSeparator /><div className="px-2 py-4 text-center text-xs text-muted-foreground">You are all caught up.</div></DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost" className="h-9 gap-2 px-1.5" aria-label="Open workspace menu"><Avatar className="size-7"><AvatarFallback className="bg-primary/15 text-xs font-semibold text-primary">CW</AvatarFallback></Avatar><ChevronDown className="hidden size-3.5 text-muted-foreground sm:block" /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56"><DropdownMenuLabel><span className="block">{session?.username ?? "CodeWise account"}</span><span className="font-normal text-muted-foreground">Authenticated Django session</span></DropdownMenuLabel><DropdownMenuSeparator /><DropdownMenuItem asChild><WorkspaceNavLink to="/settings"><Settings2 />Settings</WorkspaceNavLink></DropdownMenuItem><DropdownMenuItem onClick={() => openDialog("help", { currentTarget: document.querySelector('[aria-label="Open workspace menu"]') })}><CircleHelp />Help</DropdownMenuItem><DropdownMenuSeparator /><DropdownMenuItem onSelect={(event) => { event.preventDefault(); doSignOut(); }}>Sign out</DropdownMenuItem></DropdownMenuContent>
          </DropdownMenu>
        </header>
        <main id="main-content" tabIndex={-1} className="w-full flex-1 px-4 py-8 sm:px-6 sm:py-10 lg:px-10">
          {sessionError && <p role="alert" className="mx-auto mb-4 max-w-6xl rounded-lg bg-destructive/5 p-3 text-sm text-destructive">{sessionError}</p>}
          <Outlet />
          <footer className="mx-auto mt-14 flex max-w-6xl flex-col justify-between gap-2 border-t border-border/70 pt-5 text-xs text-muted-foreground sm:flex-row sm:items-center"><span>CodeWise V2 <span aria-hidden="true">·</span> Repository intelligence</span><span className="flex items-center gap-1.5"><Activity className="size-3.5" /> API status is checked from this workspace</span></footer>
        </main>
      </SidebarInset>
      <Dialog open={Boolean(dialog)} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent onCloseAutoFocus={restoreDialogFocus}>
          <DialogHeader>
            <DialogTitle>{dialog === "search" ? "Search workspace" : dialog === "connect" ? "Connect a repository" : "CodeWise help"}</DialogTitle>
            <DialogDescription>
              {dialog === "search" ? "Workspace search is planned and is not available yet." : dialog === "connect" ? "GitHub connection is planned. No account access or repository connection has been configured." : "CodeWise V2 is in its foundation stage. Workspace features will appear here as they become available."}
            </DialogDescription>
          </DialogHeader>
          {dialog === "search" && <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-2.5 text-sm text-muted-foreground"><Search className="size-4" /><span>Search is coming soon</span></div>}
          {dialog === "connect" && <div className="rounded-lg border border-border bg-muted/50 p-3 text-sm leading-5 text-muted-foreground"><FileCode2 className="mb-2 size-4 text-primary" />Your code remains in your repository. Connection flows will be added in a future release.</div>}
        </DialogContent>
      </Dialog>
    </SidebarProvider>
  );
}
