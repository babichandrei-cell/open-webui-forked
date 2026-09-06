<script lang="ts">
	import { onDestroy } from 'svelte';

	import { settings, terminalServers, selectedTerminalId } from '$lib/stores';
	import { downloadFileBlob } from '$lib/apis/terminal';

	export let file: any;
	export let chatId: string | null = null;
	export let imageClassName = '';
	export let alt = '';

	let objectUrl = '';
	let loading = false;
	let failed = false;
	let activeLoadKey = '';

	const revokeObjectUrl = () => {
		if (objectUrl) {
			URL.revokeObjectURL(objectUrl);
			objectUrl = '';
		}
	};

	const getPath = () => file?.path ?? file?.url ?? file?.id ?? '';

	const resolveTerminal = () => {
		const selector = file?.terminal_id ?? $selectedTerminalId;
		if (!selector) return null;

		const systemTerminal =
			($terminalServers ?? []).find((terminal: any) => terminal.id === selector) ?? null;

		if (systemTerminal?.url) {
			return {
				url: systemTerminal.url,
				key: localStorage.token
			};
		}

		const settingsValue: any = $settings;
		const userTerminal =
			(settingsValue?.terminalServers ?? []).find(
				(terminal: any) => terminal.url === selector && terminal.enabled
			) ?? null;

		if (userTerminal?.url) {
			return {
				url: userTerminal.url,
				key: userTerminal.key ?? ''
			};
		}

		return null;
	};

	const loadImage = async () => {
		const path = getPath();
		const terminal = resolveTerminal();

		if (!path || !terminal?.url || !terminal?.key) {
			failed = true;
			return;
		}

		const loadKey = `${terminal.url}|${path}|${chatId ?? ''}`;
		if (loadKey === activeLoadKey) return;

		activeLoadKey = loadKey;
		loading = true;
		failed = false;

		const result = await downloadFileBlob(
			terminal.url,
			terminal.key,
			path,
			chatId || undefined
		).catch(() => null);

		if (activeLoadKey !== loadKey) return;

		loading = false;

		if (!result?.blob) {
			failed = true;
			return;
		}

		revokeObjectUrl();
		objectUrl = URL.createObjectURL(result.blob);
	};

	$: {
		file;
		$selectedTerminalId;
		$terminalServers;
		$settings;
		void loadImage();
	}

	onDestroy(() => {
		activeLoadKey = '';
		revokeObjectUrl();
	});
</script>

{#if objectUrl}
	<img src={objectUrl} {alt} class={imageClassName} />
{:else}
	<div
		class="{imageClassName} flex items-center justify-center bg-gray-100 dark:bg-gray-800 text-gray-400"
		aria-label={failed ? 'Unable to load image preview' : 'Loading image preview'}
	>
		{#if loading}
			<span class="text-[0.625rem]">•••</span>
		{:else}
			<span class="text-[0.625rem]">IMG</span>
		{/if}
	</div>
{/if}
