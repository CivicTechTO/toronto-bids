(ns frontend.details
	(:gen-class)
	(:require [clj-http.client :as client])
	(:require [clojure.data.json :as json])
	(:require [clojure.string :as str])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.element :as elem])
	(:require [hiccup.form :as form])
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

(defn details-body [api-base query-params]
	[:div#wrapper
		(selection/selection-form api-base {} common/title)
		[:div#content
			(let 
				[
					call (common/api-call api-base "details.json" (select-keys query-params ["document_id"]))
					attachments (common/api-call api-base "attachments.json" (select-keys call ["call_number"]))
				]
				(list
					(calls/call-lines call)
					[:table#details
						(show-call-detail call "type" "Type")
						(show-call-detail call "posted_date" "Posted date")
						(show-call-detail call "site_meeting" "Site meeting")
						(show-call-detail call "buyer" "Buyer")
						(show-call-detail call "description" "Description")
						;(show-call-detail call "search_text" "Search text")
					]
					[:div#attachments
						[:div [:b "Attachments:"]]
						(if (empty? attachments)
							"none"
							(show-attachment-list (get call "call_number") attachments)
						)
					]
					[:a.back {:href (util/url "calls.html" (dissoc query-params "document_id"))} "< Back to results"]
					[:a.forward {:href (util/url "call.html" (select-keys query-params ["document_id"]))} "Permalink"]
				)
			)
		]
	]
)

(defn output-page [api-base query-params]
	(page/html5 (list (common/head "Details" "calls.css") [:body (details-body api-base query-params)]))
)

(defn output
	([api-base document_id] (output-page api-base (assoc common/default-query-params "document_id" document_id)))
	([api-base document_id division type commodity commodity_type buyer posting_date_before posting_date_after
								closing_date_before closing_date_after search_text limit-arg offset-arg direction]
		(try
			(let
				[
					limit (common/parse-limit "limit" (common/set-default common/DEFAULT-LIMIT limit-arg))
					offset-int (common/parse-limit "offset" (common/set-default common/DEFAULT-OFFSET offset-arg))
					offset (direction limit offset-int)
					query-params (assoc (common/make-query-params division type commodity commodity_type buyer posting_date_before posting_date_after 
						closing_date_before closing_date_after search_text limit offset)
						"document_id" document_id)
				]
				(output-page api-base query-params)
			)
			(catch Exception error
				(page/html5 (list (common/head common/title "calls.css") [:body error]))
			)
		)
	)
)
