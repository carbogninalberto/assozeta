<script lang="ts">
	import { page } from '$app/state';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import Menu from '@lucide/svelte/icons/menu';
	import * as Accordion from '$lib/components/ui/accordion';
	import { Button } from '$lib/components/ui/button';
	import * as NavigationMenu from '$lib/components/ui/navigation-menu';
	import { navigationMenuTriggerStyle } from '$lib/components/ui/navigation-menu/navigation-menu-trigger.svelte';
	import * as Sheet from '$lib/components/ui/sheet';
	import Github from '$lib/components/GithubIcon.svelte';
	import Logo from '$lib/components/Logo.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import { LINKS } from '$lib/config/site';
	import { NAV_GROUPS } from '$lib/data/navigation';

	let scrolled = $state(false);
	let menuOpen = $state(false);

	// On the homepage, section links scroll in place; elsewhere they are routes.
	const isHome = $derived(page.url.pathname === '/');
	const pricingHref = $derived(isHome ? '#prezzi' : '/#prezzi');
	const installHref = '/self-hosting/installazione';

	function onScroll() {
		scrolled = window.scrollY > 12;
	}

	function closeMenu() {
		menuOpen = false;
	}
</script>

<svelte:window onscroll={onScroll} />

<a
	href="#main"
	class="sr-only fixed top-3 left-3 z-[60] rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground focus:not-sr-only"
	>Vai al contenuto</a
>

<header
	class="fixed inset-x-0 top-0 z-50 transition-all duration-300 {scrolled || menuOpen
		? 'border-b border-border bg-background/80 backdrop-blur-xl'
		: 'border-b border-transparent bg-transparent'}"
>
	<nav aria-label="Principale" class="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-6">
		<a href="/" class="flex shrink-0 items-center gap-2" aria-label="Assozeta, home">
			<Logo />
		</a>

		<!-- Desktop menus -->
		<NavigationMenu.Root class="hidden lg:flex" viewport={false}>
			<NavigationMenu.List class="gap-0.5">
				{#each NAV_GROUPS as group}
					<NavigationMenu.Item>
						<NavigationMenu.Trigger
							class="bg-transparent text-muted-foreground hover:text-foreground data-open:text-foreground"
						>
							{group.label}
						</NavigationMenu.Trigger>
						<NavigationMenu.Content class="!bg-popover/95 backdrop-blur-xl">
							<ul class="grid w-[38rem] grid-cols-2 gap-1 p-2">
								{#each group.links as link}
									<li>
										<NavigationMenu.Link href={link.href} class="block h-full">
											<span class="block text-sm font-medium text-foreground">{link.label}</span>
											{#if link.description}
												<span class="mt-0.5 line-clamp-2 block text-xs leading-snug text-muted-foreground"
													>{link.description}</span
												>
											{/if}
										</NavigationMenu.Link>
									</li>
								{/each}
							</ul>
							{#if group.footer}
								<div class="border-t border-border px-2 py-2">
									<NavigationMenu.Link
										href={group.footer.href}
										target={group.footer.external ? '_blank' : undefined}
										rel={group.footer.external ? 'noopener' : undefined}
										class="flex items-center gap-1 text-sm font-medium text-brand hover:text-brand-strong"
									>
										{group.footer.label}
										{#if group.footer.external}<ArrowUpRight class="size-3.5" />{:else}<ArrowRight
												class="size-3.5"
											/>{/if}
									</NavigationMenu.Link>
								</div>
							{/if}
						</NavigationMenu.Content>
					</NavigationMenu.Item>
				{/each}
				<NavigationMenu.Item>
					<NavigationMenu.Link
						href={pricingHref}
						class={navigationMenuTriggerStyle({
							class: 'bg-transparent text-muted-foreground hover:text-foreground'
						})}
					>
						Prezzi
					</NavigationMenu.Link>
				</NavigationMenu.Item>
			</NavigationMenu.List>
		</NavigationMenu.Root>

		<!-- Desktop actions -->
		<div class="hidden items-center gap-2 lg:flex">
			<ThemeToggle />
			<Button variant="ghost" href={LINKS.github} target="_blank" rel="noopener">
				<Github />
				GitHub
			</Button>
			<Button href={installHref} class="bg-primary text-primary-foreground hover:bg-primary-hover">Installa</Button>
		</div>

		<!-- Mobile: theme toggle stays visible, next to the menu button -->
		<div class="flex items-center gap-1 lg:hidden">
			<ThemeToggle />
			<Sheet.Root bind:open={menuOpen}>
				<Sheet.Trigger>
					{#snippet child({ props })}
						<Button {...props} variant="ghost" aria-label="Apri il menu">
							<Menu class="size-5" />
						</Button>
					{/snippet}
				</Sheet.Trigger>
				<Sheet.Content side="right" class="w-[min(22rem,100vw)] overflow-y-auto">
					<Sheet.Header>
						<Sheet.Title>Menu</Sheet.Title>
					</Sheet.Header>
					<div class="px-4 pb-6">
						<Accordion.Root type="multiple">
							{#each NAV_GROUPS as group}
								<Accordion.Item value={group.label}>
									<Accordion.Trigger>{group.label}</Accordion.Trigger>
									<Accordion.Content>
										<ul class="space-y-0.5">
											{#each group.links as link}
												<li>
													<a
														href={link.href}
														onclick={closeMenu}
														class="block rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
														>{link.label}</a
													>
												</li>
											{/each}
											{#if group.footer}
												<li>
													<a
														href={group.footer.href}
														target={group.footer.external ? '_blank' : undefined}
														rel={group.footer.external ? 'noopener' : undefined}
														onclick={closeMenu}
														class="flex items-center gap-1 rounded-md px-2 py-1.5 text-sm font-medium text-brand"
													>
														{group.footer.label}
														<ArrowRight class="size-3.5" />
													</a>
												</li>
											{/if}
										</ul>
									</Accordion.Content>
								</Accordion.Item>
							{/each}
						</Accordion.Root>
						<div class="mt-4 space-y-1">
							<a
								href={pricingHref}
								onclick={closeMenu}
								class="block rounded-md px-2 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
								>Prezzi</a
							>
							<a
								href={LINKS.github}
								target="_blank"
								rel="noopener"
								onclick={closeMenu}
								class="flex items-center gap-1.5 rounded-md px-2 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
							>
								<Github /> GitHub
							</a>
						</div>
						<Button
							href={installHref}
							onclick={closeMenu}
							class="mt-4 w-full bg-primary text-primary-foreground hover:bg-primary-hover"
							>Installa Assozeta</Button
						>
					</div>
				</Sheet.Content>
			</Sheet.Root>
		</div>
	</nav>
</header>
