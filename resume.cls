\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{resume}[2026/06/20 Minimal resume class]
\newif\ifresumeliteraltext
\DeclareOption{literaltext}{\resumeliteraltexttrue}
\ProcessOptions\relax
\LoadClass[11pt]{article}

\setlength{\parindent}{0pt}

\RequirePackage{fontspec}
\RequirePackage{xeCJK}
\RequirePackage{xcolor}
\RequirePackage{hyperref}
\RequirePackage{url}
\RequirePackage{geometry}
\RequirePackage{titlesec}
\RequirePackage{enumitem}
\RequirePackage{fontawesome}

\hypersetup{hidelinks}
\urlstyle{same}

% JSON renderer input is literal text, including CLI flags and apostrophes.
% Handwritten templates retain normal TeX punctuation conventions.
\ifresumeliteraltext
  \defaultfontfeatures[\rmfamily]{Ligatures=TeXReset}
\fi
\IfFontExistsTF{Times New Roman}
  {\setmainfont{Times New Roman}}
  {\setmainfont{TeX Gyre Termes}}

\setCJKmainfont[
  Path = fonts/NotoSansSC/ ,
  BoldFont = NotoSansSC-Bold.otf ,
  ItalicFont = NotoSansSC-Regular.otf
]{NotoSansSC-Regular.otf}
\setCJKsansfont[
  Path = fonts/NotoSansSC/ ,
  BoldFont = NotoSansSC-Bold.otf
]{NotoSansSC-Regular.otf}
\setCJKmonofont[
  Path = fonts/NotoSansSC/
]{NotoSansSC-Regular.otf}

\geometry{
  a4paper,
  left=0.72in,
  right=0.72in,
  top=0.58in,
  bottom=0.52in,
  nohead
}

\setlist{noitemsep}
\setlist[itemize]{topsep=0.08em, leftmargin=1.5pc}
\setlist[enumerate]{topsep=0.25em, leftmargin=1.5pc}

\definecolor{resumeText}{HTML}{111111}
\definecolor{resumeMuted}{HTML}{333333}
\definecolor{resumeRule}{HTML}{777777}
\color{resumeText}

\titleformat{\section}
  {\large\bfseries\raggedright}
  {}{0em}
  {}
  [\color{resumeRule}\titlerule]
\titlespacing*{\section}{0cm}{*1.45}{*1.05}

\titleformat{\subsection}
  {\normalsize\bfseries\raggedright}
  {}{0em}
  {}
\titlespacing*{\subsection}{0cm}{*1.2}{*0.35}

\newcommand{\name}[1]{%
  {\par\centering\Huge\bfseries #1\par}%
  \vspace{0.85ex}
}

\newcommand{\contacttext}[1]{{\rmfamily\normalsize\textcolor{resumeMuted}{#1}}}
\newcommand{\email}[1]{\faEnvelope\ \href{mailto:#1}{\contacttext{#1}}}
\newcommand{\phone}[1]{\faPhone\ \contacttext{#1}}
\newcommand{\github}[1]{\faGithub\ \href{https://#1}{\contacttext{#1}}}

\newcommand{\basicInfo}[1]{%
  {\par\centering #1\par}%
  \vspace{0.65ex}
}

\newcommand{\headline}[1]{%
  {\par\centering\normalsize\textcolor{resumeMuted}{#1}\par}%
  \vspace{1.15ex}
}

\newcommand{\datedline}[2]{%
  {\par #1 \hfill #2 \par}%
}

\newcommand{\datedsubsection}[2]{%
  \subsection[#1]{#1 \hfill #2}%
}
