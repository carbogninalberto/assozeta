package handler

// These are the six DRF transport messages used by the manifest and
// collaborator list. They were extracted from DRF 3.17.2's published
// rest_framework/locale catalogs using CPython's locale expansion, which merges
// every matching catalog in expansion order. Django then merges the project
// default Italian catalog, so an absent individual message keeps the Italian
// text while the response Content-Language remains the selected code.
// Django 5.2.18 and DRF 3.17.2 are BSD-3-Clause; DRF is Copyright 2011-2023
// Encode OSS Ltd. See BE_GO/THIRD_PARTY_NOTICES.md for provenance and licenses.

type readRouteMessages struct {
	credentials string
	permission  string
	accept      string
	notFound    string
	method      string
	media       string
}

var readRouteMessageCatalogs = map[string]readRouteMessages{
	"it": {
		credentials: `Non sono state immesse le credenziali di autenticazione.`,
		permission:  `Non hai l'autorizzazione per eseguire questa azione.`,
		accept:      `Impossibile soddisfare l'header "Accept" presente nella richiesta.`,
		notFound:    `Non trovato.`,
		method:      `Metodo "{method}" non consentito`,
		media:       `Tipo di media "{media_type}"non supportato.`,
	},
	"ar": {
		credentials: `لم يتم تزويد بيانات الدخول.`,
		permission:  `ليس لديك صلاحية للقيام بهذا الإجراء.`,
		accept:      `تعذر تلبية ترويسة Accept في الطلب.`,
		notFound:    `غير موجود.`,
		method:      `طريقة "{method}" غير مسموح بها.`,
		media:       `الوسيط "{media_type}" الموجود في الطلب غير معتمد.`,
	},
	"az": {
		credentials: `Təsdiqləmə məlumatları təchiz edilməyib.`,
		permission:  `Bu əməliyyat üçün icazəniz yoxdur.`,
		accept:      `İstəyin Accept başlığını qane etmək mümkün olmadı.`,
		notFound:    `Tapılmadı.`,
		method:      `"{method}" yöntəminə icazə verilmir.`,
		media:       `İstəkdə dəstəklənməyən "{media_type}" mediya növü.`,
	},
	"bg": {
		credentials: `Носители на удостоверение не са предоставени.`,
		permission:  `Нямате права да се направи това действие.`,
		accept:      `Не може да бъде удовлетворен Accept header.`,
		notFound:    `Обектът не е намерен.`,
		method:      `Метод "{method}" не е позволен.`,
		media:       `Неподдържан тип на документа "{media_type}" в заявката.`,
	},
	"ca": {
		credentials: `Credencials d'autenticació no disponibles.`,
		permission:  `No té permisos per realitzar aquesta acció.`,
		accept:      `No s'ha pogut satisfer l'Accept header de la petició.`,
		notFound:    `No trobat.`,
		method:      `Mètode "{method}" no permès.`,
		media:       `Media type "{media_type}" no suportat.`,
	},
	"cs": {
		credentials: `Nebyly zadány přihlašovací údaje.`,
		permission:  `K této akci nemáte oprávnění.`,
		accept:      `Nelze vyhovět požadavku v hlavičce Accept.`,
		notFound:    `Nenalezeno.`,
		method:      `Metoda "{method}" není povolena.`,
		media:       `Nepodporovaný media type "{media_type}" v požadavku.`,
	},
	"da": {
		credentials: `Legitimation til autentificering blev ikke angivet.`,
		permission:  `Du har ikke lov til at udføre denne handling.`,
		accept:      `Kunne ikke efterkomme forespørgslens Accept header.`,
		notFound:    `Ikke fundet.`,
		method:      `Metoden "{method}" er ikke tilladt.`,
		media:       `Forespørgslens media type, "{media_type}", er ikke understøttet.`,
	},
	"de": {
		credentials: `Anmeldedaten fehlen.`,
		permission:  `Sie sind nicht berechtigt, diese Aktion durchzuführen.`,
		accept:      `Kann die Accept Kopfzeile der Anfrage nicht erfüllen.`,
		notFound:    `Nicht gefunden.`,
		method:      `Methode "{method}" nicht erlaubt.`,
		media:       `Nicht unterstützter Medientyp "{media_type}" in der Anfrage.`,
	},
	"el": {
		credentials: `Δεν δόθηκαν διαπιστευτήρια.`,
		permission:  `Δεν έχετε δικαίωματα για αυτή την ενέργεια.`,
		accept:      `Δεν ήταν δυνατή η ικανοποίηση της επικεφαλίδας Accept της αίτησης.`,
		notFound:    `Δε βρέθηκε.`,
		method:      `Η μέθοδος "{method}" δεν επιτρέπεται.`,
		media:       `Δεν υποστηρίζεται το media type "{media_type}" της αίτησης.`,
	},
	"en": {
		credentials: `Authentication credentials were not provided.`,
		permission:  `You do not have permission to perform this action.`,
		accept:      `Could not satisfy the request Accept header.`,
		notFound:    `Not found.`,
		method:      `Method "{method}" not allowed.`,
		media:       `Unsupported media type "{media_type}" in request.`,
	},
	"es": {
		credentials: `Las credenciales de autenticación no se proveyeron.`,
		permission:  `Usted no tiene permiso para realizar esta acción.`,
		accept:      `No se ha podido satisfacer la solicitud de cabecera de Accept.`,
		notFound:    `No encontrado.`,
		method:      `Método "{method}" no permitido.`,
		media:       `Tipo de medio "{media_type}" incompatible en la solicitud.`,
	},
	"et": {
		credentials: `Autentimistunnus on esitamata.`,
		permission:  `Teil puuduvad piisavad õigused selle tegevuse teostamiseks.`,
		accept:      `Päringu Accept-päist ei suutnud täita.`,
		notFound:    `Ei leidnud.`,
		method:      `Meetod "{method}" pole lubatud.`,
		media:       `Meedia tüüpi {media_type} päringus ei toetata.`,
	},
	"fa": {
		credentials: `اطلاعات برای اعتبارسنجی ارسال نشده است.`,
		permission:  `شما اجازه انجام این دستور را ندارید.`,
		accept:      `نوع محتوای درخواستی در هدر قابل قبول نیست.`,
		notFound:    `یافت نشد.`,
		method:      `متد {method} مجاز نیست.`,
		media:       `نوع رسانه {media_type} در درخواست پشتیبانی نمیشود.`,
	},
	"fi": {
		credentials: `Autentikaatiotunnuksia ei annettu.`,
		permission:  `Sinulla ei ole oikeutta suorittaa tätä toimintoa.`,
		accept:      `Ei voitu vastata pyynnön Accept-otsakkeen mukaisesti.`,
		notFound:    `Ei löydy.`,
		method:      `Metodi "{method}" ei ole sallittu.`,
		media:       `Pyynnön mediatyyppiä "{media_type}" ei tueta.`,
	},
	"fr": {
		credentials: `Informations d'authentification non fournies.`,
		permission:  `Vous n'avez pas la permission d'effectuer cette action.`,
		accept:      `L'en-tête « Accept » n'a pas pu être satisfaite.`,
		notFound:    `Non trouvé.`,
		method:      `Méthode « {method} » non autorisée.`,
		media:       `Type de média « {media_type} » non supporté.`,
	},
	"hu": {
		credentials: `Nem voltak megadva azonosítók.`,
		permission:  `Nincs jogosultsága a művelet végrehajtásához.`,
		accept:      `A kérés Accept fejlécét nem lehetett teljesíteni.`,
		notFound:    `Nem található.`,
		method:      `A "{method}" metódus nem megengedett.`,
		media:       `Nem támogatott média típus "{media_type}" a kérésben.`,
	},
	"hy": {
		credentials: `Նույնականացման տվյալները տրամադրված չեն:`,
		permission:  `Այս գործողությունը կատարելու թույլտվություն չունեք:`,
		accept:      `Չհաջողվեց բավարարել հարցման Ընդունել վերնագիրը:`,
		notFound:    `Չի գտնվել։`,
		method:      `"{method}" մեթոդը թույլատրված չէ:`,
		media:       `Անհամապատասխան մեդիա տիպ "{media_type}":`,
	},
	"id": {
		credentials: `Data autentikasi tidak diberikan.`,
		permission:  `Anda tidak memiliki izin untuk melakukan tindakan ini.`,
		accept:      `Permintaan dengan header Accept ini tidak dapat dipenuhi.`,
		notFound:    `Data tidak ditemukan.`,
		method:      `Metodo "{method}" non consentito`,
		media:       `Jenis media "{media_type}" dalam permintaan ini tidak didukung.`,
	},
	"ja": {
		credentials: `認証情報が含まれていません。`,
		permission:  `このアクションを実行する権限がありません。`,
		accept:      `リクエストのAcceptヘッダを満たすことができませんでした。`,
		notFound:    `見つかりませんでした。`,
		method:      `メソッド "{method}" は許されていません。`,
		media:       `リクエストのメディアタイプ "{media_type}" はサポートされていません。`,
	},
	"kk": {
		credentials: `Аутентификация деректері берілмеген.`,
		permission:  `Бұл әрекетті орындауға рұқсатыңыз жоқ.`,
		accept:      `Сұраныстағы Accept тақырыбын қанағаттандыру мүмкін емес.`,
		notFound:    `Табылмады.`,
		method:      `"{method}" әдісіне рұқсат етілмейді.`,
		media:       `Сұраныстағы "{media_type}" медиа түрі қолдау көрсетілмейді.`,
	},
	"ko": {
		credentials: `자격 인증 데이터가 제공되지 않았습니다.`,
		permission:  `이 작업을 수행할 권한이 없습니다.`,
		accept:      `요청 Accept 헤더를 만족시킬 수 없습니다.`,
		notFound:    `찾을 수 없습니다.`,
		method:      `메서드 "{method}"는 허용되지 않습니다.`,
		media:       `요청된 "{media_type}"가 지원되지 않는 미디어 형태입니다.`,
	},
	"lt": {
		credentials: `Autentifikacijos duomenys nesuteikti.`,
		permission:  `Non hai l'autorizzazione per eseguire questa azione.`,
		accept:      `Nepavyko patenkinti užklausos Accept antraštės.`,
		notFound:    `Nerasta.`,
		method:      `Metodas "{method}" yra neleidžiamas.`,
		media:       `Nepalaikomas duomenų formatas "{media_type}" užklausoje.`,
	},
	"lv": {
		credentials: `Netika nodrošināti autorizācijas parametri.`,
		permission:  `Tev nav tiesību veikt šo darbību.`,
		accept:      `Nevarēja apmierināt pieprasījuma Accept header.`,
		notFound:    `Nav atrasts.`,
		method:      `Metode "{method}" nav atļauta.`,
		media:       `Pieprasījumā neatbalstīts datu tips "{media_type}" .`,
	},
	"mk": {
		credentials: `Не се внесени податоци за најава.`,
		permission:  `Немате дозвола да го сторите ова.`,
		accept:      `Не може да се исполни барањето на Accept header-от.`,
		notFound:    `Не е пронајдено ништо.`,
		method:      `Методата "{method}" не е дозволена.`,
		media:       `Media типот „{media_type}“ не е поддржан.`,
	},
	"nb": {
		credentials: `Manglende autentiseringsinformasjon.`,
		permission:  `Du har ikke tilgang til å utføre denne handlingen.`,
		accept:      `Kunne ikke tilfredsstille request Accept header.`,
		notFound:    `Ikke funnet.`,
		method:      `Metoden "{method}" ikke gyldig.`,
		media:       `Ugyldig media type "{media_type}" i request.`,
	},
	"nl": {
		credentials: `Authenticatiegegevens zijn niet opgegeven.`,
		permission:  `Je hebt geen toestemming om deze actie uit te voeren.`,
		accept:      `Kan niet voldoen aan de opgegeven Accept header.`,
		notFound:    `Niet gevonden.`,
		method:      `Methode "{method}" niet toegestaan.`,
		media:       `Ongeldige media type "{media_type}" in aanvraag.`,
	},
	"pl": {
		credentials: `Nie podano danych uwierzytelniających.`,
		permission:  `Nie masz uprawnień, by wykonać tę czynność.`,
		accept:      `Nie można zaspokoić nagłówka Accept żądania.`,
		notFound:    `Nie znaleziono.`,
		method:      `Niedozwolona metoda "{method}".`,
		media:       `Brak wsparcia dla żądanego typu danych "{media_type}".`,
	},
	"pt": {
		credentials: `As credenciais de autenticação não foram fornecidas.`,
		permission:  `Você não tem permissão para executar essa ação.`,
		accept:      `Não foi possível satisfazer a requisição do cabeçalho Accept.`,
		notFound:    `Não encontrado.`,
		method:      `Método "{method}" não é permitido.`,
		media:       `Tipo de mídia  "{media_type}" no pedido não é suportado.`,
	},
	"pt-br": {
		credentials: `As credenciais de autenticação não foram fornecidas.`,
		permission:  `Você não tem permissão para executar essa ação.`,
		accept:      `Não foi possível satisfazer a requisição do cabeçalho Accept.`,
		notFound:    `Não encontrado.`,
		method:      `Método "{method}" não é permitido.`,
		media:       `Tipo de mídia "{media_type}" não é suportado.`,
	},
	"ro": {
		credentials: `Datele de autentificare nu au fost furnizate.`,
		permission:  `Nu aveți permisiunea de a efectua această acțiune.`,
		accept:      `Antetul Accept al cererii nu a putut fi îndeplinit.`,
		notFound:    `Nu a fost găsit(ă).`,
		method:      `Metoda "{method}" nu este permisa.`,
		media:       `Cererea conține tipul media neacceptat "{media_type}"`,
	},
	"ru": {
		// CPython expands ru to ru_RU before ru and merges both catalogs:
		// permission and notFound come from ru_RU; the other messages use ru.
		credentials: `Учетные данные не были предоставлены.`,
		permission:  `У вас недостаточно прав для выполнения данного действия.`,
		accept:      `Невозможно удовлетворить "Accept" заголовок запроса.`,
		notFound:    `Страница не найдена.`,
		method:      `Метод "{method}" не разрешен.`,
		media:       `Неподдерживаемый тип данных "{media_type}" в запросе.`,
	},
	"sk": {
		credentials: `Prihlasovacie údaje neboli zadané.`,
		permission:  `K danej akcii nemáte oprávnenie.`,
		accept:      `Nie je možné vyhovieť požiadavku v hlavičke "Accept".`,
		notFound:    `Nebolo nájdené.`,
		method:      `Metóda "{method}" nie je povolená.`,
		media:       `Požiadavok obsahuje nepodporovaný media type: "{media_type}".`,
	},
	"sl": {
		credentials: `Avtentikacijski podatki niso bili podani.`,
		permission:  `Nimate dovoljenj za izvedbo te akcije.`,
		accept:      `Ni bilo mogoče zagotoviti zaglavja Accept zahtevka.`,
		notFound:    `Ni najdeno`,
		method:      `Metoda "{method}" ni dovoljena`,
		media:       `Nepodprt medijski tip "{media_type}" v zahtevku.`,
	},
	"sv": {
		credentials: `Autentiseringsuppgifter ej tillhandahållna.`,
		permission:  `Du har inte tillåtelse att utföra denna förfrågan.`,
		accept:      `Kunde inte tillfredsställa förfrågans "Accept"-header.`,
		notFound:    `Hittades inte.`,
		method:      `Metoden "{method}" tillåts inte.`,
		media:       `Medietypen "{media_type}" stöds inte.`,
	},
	"th": {
		credentials: `ไม่พบข้อมูลการเข้าสู่ระบบ`,
		permission:  `คุณไม่มีสิทธิ์ที่จะดำเนินการ`,
		accept:      `Impossibile soddisfare l'header "Accept" presente nella richiesta.`,
		notFound:    `ไม่พบ`,
		method:      `ไม่ใช่อนุญาติให้ใช้ Method "{method}"`,
		media:       `ไม่รองรับมีเดียประเภท "{media_type}" ใน Request`,
	},
	"tr": {
		credentials: `Giriş bilgileri verilmedi.`,
		permission:  `Bu işlemi yapmak için izniniz bulunmuyor.`,
		accept:      `İsteğe ait Accept başlık bilgisi yanıt verilecek başlık bilgileri arasında değil.`,
		notFound:    `Bulunamadı.`,
		method:      `"{method}" metoduna izin verilmiyor.`,
		media:       `İstekte desteklenmeyen medya tipi: "{media_type}".`,
	},
	"uk": {
		credentials: `Реквізити перевірки достовірності не надані.`,
		permission:  `У вас нема дозволу робити цю дію.`,
		accept:      `Неможливо виконати запит заголовку Accept.`,
		notFound:    `Не знайдено.`,
		method:      `Метод "{method}" не дозволений.`,
		media:       `Непідтримуваний тип даних "{media_type}" в запиті.`,
	},
	"vi": {
		credentials: `Non sono state immesse le credenziali di autenticazione.`,
		permission:  `Bạn không được cấp quyền để thực hiện hành động này.`,
		accept:      `Impossibile soddisfare l'header "Accept" presente nella richiesta.`,
		notFound:    `Không tìm thấy.`,
		method:      `Phương thức “{method}” không được chấp nhận.`,
		media:       `Tipo di media "{media_type}"non supportato.`,
	},
	"zh-hans": {
		credentials: `身份认证信息未提供。`,
		permission:  `您没有执行该操作的权限。`,
		accept:      `无法满足Accept HTTP头的请求。`,
		notFound:    `未找到。`,
		method:      `方法 “{method}” 不被允许。`,
		media:       `不支持请求中的媒体类型 “{media_type}”。`,
	},
	"zh-hant": {
		credentials: `Non sono state immesse le credenziali di autenticazione.`,
		permission:  `你沒有執行此操作的權限。`,
		accept:      `Impossibile soddisfare l'header "Accept" presente nella richiesta.`,
		notFound:    `找不到資源。`,
		method:      `不被允許的方法 "{method}"。`,
		media:       `Tipo di media "{media_type}"non supportato.`,
	},
}

func readRouteMessagesForLanguage(language string) readRouteMessages {
	var catalog string
	switch language {
	case "ar", "ar-dz":
		catalog = "ar"
	case "az":
		catalog = "az"
	case "bg":
		catalog = "bg"
	case "ca":
		catalog = "ca"
	case "cs":
		catalog = "cs"
	case "da":
		catalog = "da"
	case "de":
		catalog = "de"
	case "el":
		catalog = "el"
	case "en", "en-au", "en-gb":
		catalog = "en"
	case "es", "es-ar", "es-co", "es-mx", "es-ni", "es-ve":
		catalog = "es"
	case "et":
		catalog = "et"
	case "fa":
		catalog = "fa"
	case "fi":
		catalog = "fi"
	case "fr":
		catalog = "fr"
	case "hu":
		catalog = "hu"
	case "hy":
		catalog = "hy"
	case "id":
		catalog = "id"
	case "ja":
		catalog = "ja"
	case "kk":
		catalog = "kk"
	case "ko":
		catalog = "ko"
	case "lt":
		catalog = "lt"
	case "lv":
		catalog = "lv"
	case "mk":
		catalog = "mk"
	case "nb":
		catalog = "nb"
	case "nl":
		catalog = "nl"
	case "pl":
		catalog = "pl"
	case "pt":
		catalog = "pt"
	case "pt-br":
		catalog = "pt-br"
	case "ro":
		catalog = "ro"
	case "ru":
		catalog = "ru"
	case "sk":
		catalog = "sk"
	case "sl":
		catalog = "sl"
	case "sv":
		catalog = "sv"
	case "th":
		catalog = "th"
	case "tr":
		catalog = "tr"
	case "uk":
		catalog = "uk"
	case "vi":
		catalog = "vi"
	case "zh-hans":
		catalog = "zh-hans"
	case "zh-hant":
		catalog = "zh-hant"
	default:
		catalog = "it"
	}
	return readRouteMessageCatalogs[catalog]
}
