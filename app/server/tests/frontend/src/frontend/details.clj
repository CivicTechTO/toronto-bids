(ns frontend.details
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [hiccup.page :as page])
	(:require [hiccup.element :as elem])
	(:require [hiccup.util :as util])
	(:require [frontend.common :as common])
	(:require [frontend.calls :as calls])
	(:require [frontend.selection :as selection])
	(:import java.net.URI)
)

(def attachment-host
	{
		:scheme "https"
		:host "torontobidsstorage.file.core.windows.net"
		:path "/torontobids/ariba_data/"
	}
)
(def ariba-sas-token
	{
		:sv "2022-11-02"
		:ss "f"
		:srt "sco"
		:sp "rl"
		:se "2123-05-18T04:49:53Z"
		:st "2023-05-17T20:49:53Z"
		:spr "https,http"
		:sig "uWfbBiXayfnnSxN/pmRW/BOtVOyGcY//cz5lN8gjAP4="
	 }
)

(defn make-attachment-url [call-number file]
	(let
		[
			path (str (:path attachment-host) call-number "/" file)
			uri (URI. (:scheme attachment-host) (:host attachment-host) path nil)
		]
		(util/url (str uri) ariba-sas-token)
	)
)

(defn show-call-detail [call field field-name]
	(let [value (some-> (get call field) str/trim)]
		[:tr
			[:td [:b field-name ": "]]
			[:td (if (empty? value) "(none)" value)]
		]
	)
)

(defn show-attachment-list [call-number attachments]
	(elem/unordered-list
		(for [at attachments :let [filename (get at "filename")]]
			[:a {:href (make-attachment-url call-number filename)} filename]
		)
	)
)

(defn details-body [api-base local-base query-params]
	[:div#wrapper
		(selection/selection-form api-base local-base query-params common/title)
		[:div#content
			(let 
				[
					call-number (select-keys query-params ["call_number"])
					call (common/api-call api-base "details.json" call-number)
					attachments (common/api-call api-base "attachments.json" call-number)
				]
				(list
					(calls/call-lines call)
					[:table#details
						(show-call-detail call "type" "Type")
						(show-call-detail call "posted_date" "Posted date")
						(show-call-detail call "site_meeting" "Site meeting")
						(show-call-detail call "buyer" "Buyer")
						(show-call-detail call "description" "Description")
;						(show-call-detail call "search_text" "Search text")
					]
					[:div#attachments
						[:div [:b "Attachments:"]]
						(if (empty? attachments)
							"none"
							(show-attachment-list (get call "call_number") attachments)
						)
					]
					[:a.back {:href (util/url (str local-base "/calls.html") (dissoc query-params "call_number"))} "< Back to results"]
					[:a.forward {:href (util/url (str local-base "/call.html") call-number)} "Permalink"]
				)
			)
		]
	]
)

(defn output-page [api-base local-base query-params]
	(page/html5 (list common/head [:body (details-body api-base local-base query-params)]))
)

(defn output
	([api-base local-base call_number] (output-page api-base local-base (assoc common/default-query-params "call_number" call_number)))
	([api-base local-base call_number division type commodity commodity_type buyer
	  posting_date_before posting_date_after closing_date_before closing_date_after
	  search_text limit-arg offset-arg]
		(try
			(let
				[
					limit (common/parse-int "limit" limit-arg common/DEFAULT-LIMIT)
					offset (common/parse-int "offset" offset-arg common/DEFAULT-OFFSET)
					query-params (common/make-query-params
						division type commodity commodity_type buyer
						posting_date_before posting_date_after closing_date_before closing_date_after
						search_text limit offset
					)
				]
				(output-page api-base local-base (assoc query-params "call_number" call_number))
			)
			(catch Exception error
				(page/html5 (list common/head [:body error]))
			)
		)
	)
)
