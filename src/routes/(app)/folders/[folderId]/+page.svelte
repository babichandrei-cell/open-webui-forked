<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Chat from '$lib/components/chat/Chat.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getFolderById } from '$lib/apis/folders';
	import { selectedFolder, selectedTerminalId } from '$lib/stores';

	let ready = false;
	let workspaceManaged = false;

	const applyFolderFilesWorkspace = (folder: any) => {
		workspaceManaged = true;
		selectedTerminalId.set(folder?.data?.files_workspace?.terminal_id ?? null);
	};

	onMount(async () => {
		const folderId = $page.params.folderId;
		if (!folderId) {
			await goto('/');
			return;
		}

		// The sidebar click handler already fetches the folder and sets
		// `selectedFolder` before navigating here; refetching would duplicate
		// the request and re-trigger the sidebar's folder refresh.
		if ($selectedFolder?.id !== folderId) {
			const folder = await getFolderById(localStorage.token, folderId).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!folder) {
				await goto('/');
				return;
			}

			await selectedFolder.set(folder);
		}

		applyFolderFilesWorkspace($selectedFolder);
		ready = true;
	});

	onDestroy(() => {
		selectedFolder.set(null);
		if (workspaceManaged) {
			selectedTerminalId.set(null);
		}
	});
</script>

{#if ready}
	<Chat />
{:else}
	<div class="w-full h-screen flex items-center justify-center">
		<Spinner />
	</div>
{/if}
