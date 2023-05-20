(ns frontend.details
	(:gen-class)
	(:require [clj-http.client :as client])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.element :as elem])
	(:require [hiccup.form :as form])
	(:require [hiccup.util :as util])
	(:require [frontend.common :as common])
	(:require [frontend.calls :as calls])
	(:require [frontend.selection :as selection])
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
					[:div (get call "site_meeting")]
					[:div#description
						"<b>Description:</b>"
						[:div (get call "description")]
					]
					;[:div#fulltext (get call "search_text")]
					[:div#attachments
						"<b>Attachments:</b>"
						(elem/unordered-list (map #(get % "filename") attachments))
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
