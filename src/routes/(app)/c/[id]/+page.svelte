<script lang="ts">
	import { page } from '$app/stores';
	import { onDestroy, onMount } from 'svelte';

	import Chat from '$lib/components/chat/Chat.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getChatById } from '$lib/apis/chats';
	import { getFolderById } from '$lib/apis/folders';
	import { selectedTerminalId } from '$lib/stores';

	let ready = false;
	let workspaceManaged = false;

	onMount(async () => {
		const chat = await getChatById(localStorage.token, $page.params.id).catch(() => null);

		if (chat?.folder_id) {
			const folder = await getFolderById(localStorage.token, chat.folder_id).catch(() => null);
			if (folder) {
				workspaceManaged = true;
				selectedTerminalId.set(folder?.data?.files_workspace?.terminal_id ?? null);
			}
		}

		ready = true;
	});

	onDestroy(() => {
		if (workspaceManaged) {
			selectedTerminalId.set(null);
		}
	});
</script>

{#if ready}
	<Chat chatIdProp={$page.params.id} />
{:else}
	<div class="w-full h-screen flex items-center justify-center">
		<Spinner />
	</div>
{/if}
