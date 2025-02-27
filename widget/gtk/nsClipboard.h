/* -*- Mode: C++; tab-width: 4; indent-tabs-mode: nil; c-basic-offset: 4 -*- */
/* vim:expandtab:shiftwidth=4:tabstop=4:
 */
/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef __nsClipboard_h_
#define __nsClipboard_h_

#include "nsIClipboard.h"
#include "nsIObserver.h"
#include "nsIBinaryOutputStream.h"
#include <gtk/gtk.h>

// Default Gtk MIME for text
#define GTK_DEFAULT_MIME_TEXT "UTF8_STRING"

class nsRetrievalContext : public nsIObserver {
public:
    NS_DECL_ISUPPORTS
    NS_DECL_NSIOBSERVER

    nsRetrievalContext();

    NS_IMETHOD HasDataMatchingFlavors(const char** aFlavorList,
                                      uint32_t aLength,
                                      int32_t aWhichClipboard,
                                      bool *_retval) = 0;
    NS_IMETHOD GetClipboardContent(const char* aMimeType,
                                   int32_t aWhichClipboard,
                                   nsIInputStream** aResult,
                                   uint32_t* aContentLength) = 0;

    // Save global clipboard content to gtk
    void  Store(void);

protected:
    virtual ~nsRetrievalContext();

    // Idle timeout for receiving selection and property notify events (microsec)
    static const int kClipboardTimeout;
};

class nsClipboard : public nsIClipboard
{
public:
    nsClipboard();
    
    NS_DECL_ISUPPORTS
    
    NS_DECL_NSICLIPBOARD

    // Make sure we are initialized, called from the factory
    // constructor
    nsresult  Init              (void);

    // Someone requested the selection
    void   SelectionGetEvent    (GtkClipboard     *aGtkClipboard,
                                 GtkSelectionData *aSelectionData);
    void   SelectionClearEvent  (GtkClipboard     *aGtkClipboard);

private:
    virtual ~nsClipboard();

    // Save global clipboard content to gtk
    nsresult                     Store            (void);

    // Get our hands on the correct transferable, given a specific
    // clipboard
    nsITransferable             *GetTransferable  (int32_t aWhichClipboard);

    // Hang on to our owners and transferables so we can transfer data
    // when asked.
    nsCOMPtr<nsIClipboardOwner>  mSelectionOwner;
    nsCOMPtr<nsIClipboardOwner>  mGlobalOwner;
    nsCOMPtr<nsITransferable>    mSelectionTransferable;
    nsCOMPtr<nsITransferable>    mGlobalTransferable;
    RefPtr<nsRetrievalContext>   mContext;
};

GdkAtom GetSelectionAtom(int32_t aWhichClipboard);

#endif /* __nsClipboard_h_ */
