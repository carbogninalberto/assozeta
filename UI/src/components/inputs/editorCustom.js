
export function getOnTransaction(editor) {
	return () => {editor = editor}
}

export function getHandlePaste(editor, {validateImage = () => true, onError = message => window.alert(message)} = {}) {
	return (view, event, slice) => {
		const item = event.clipboardData?.items[0]

		if (item?.type.indexOf("image") !== 0) {
			return false;
		}
		
 		const file = item.getAsFile()
        if (!file || !validateImage(file)) return true;
		let filesize = ((file.size/1024)/1024).toFixed(4)
		
		if (filesize > 10) {
			onError('L’immagine è troppo grande. Scegli un file fino a 10 MB.')
			return true
		}
		
		let reader = new FileReader();
		reader.readAsDataURL(file);
		reader.onload = e => {
			editor.commands.setImage({src: e.target.result})
		};
		
		return true
	}
}